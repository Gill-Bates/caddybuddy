#!/usr/bin/env python3
#
# app/routers/ui.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import (
    get_session_user,
    push_flash,
    redirect_to,
    render_template,
    validate_csrf_token,
)
from app.models.entities import ApiKey, AuditLog, CaddyConfig, CaddyServer, User
from app.repositories.api_keys import api_key_repository
from app.repositories.audit_logs import audit_log_repository
from app.repositories.configs import config_repository
from app.repositories.servers import server_repository
from app.repositories.users import user_repository
from app.services.audit import audit_service
from app.services.auth import WeakPasswordError, auth_service
from app.services.caddy import caddy_service
from app.utils.parsing import parse_expires_days, parse_json_object, pretty_json, split_csv


router = APIRouter()


async def _require_user(request: Request, session: AsyncSession):
    current_user = await get_session_user(request, session)
    if current_user is None:
        push_flash(request, "warning", "Please sign in to continue.")
        return None
    return current_user


async def _require_admin(request: Request, session: AsyncSession):
    current_user = await _require_user(request, session)
    if current_user is None:
        return None
    if current_user.role != "admin":
        push_flash(request, "danger", "Administrator access is required.")
        return None
    return current_user


async def _validated_form(request: Request):
    form = await request.form()
    validate_csrf_token(request, str(form.get("csrf_token", "")))
    return form


def _safe_next(next_path: str) -> str:
    if (
        not next_path
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or next_path.startswith("/\\")
    ):
        return "/"
    return next_path


def _parse_int(value: object, *, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _load_api_keys(session: AsyncSession, current_user: User):
    if current_user.role == "admin":
        return await api_key_repository.list_all(session)
    return await api_key_repository.list_for_user(session, current_user.id)


async def _render_api_keys_page(
    request: Request,
    session: AsyncSession,
    current_user: User,
    *,
    pending_api_key: str | None = None,
    status_code: int = 200,
):
    context = {
        "page_title": "API Keys",
        "api_keys": await _load_api_keys(session, current_user),
        "show_all": current_user.role == "admin",
        "pending_api_key": pending_api_key,
    }
    response = render_template(
        request,
        "api_keys.html",
        current_user=current_user,
        context=context,
        status_code=status_code,
    )
    if pending_api_key is not None:
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


async def _audit_commit_and_flash(
    session: AsyncSession,
    request: Request,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict | None = None,
    status_code: int = 200,
    actor: User | None = None,
    flashes: Sequence[tuple[str, str]] = (),
) -> None:
    await audit_service.log_action(
        session,
        action=action,
        resource_type=resource_type,
        request=request,
        resource_id=resource_id,
        details=details or {},
        status_code=status_code,
        actor=actor,
    )
    await session.commit()
    for category, message in flashes:
        push_flash(request, category, message)


async def _load_dashboard_context(session: AsyncSession) -> dict[str, object]:
    counts = (
        await session.execute(
            select(
                select(func.count(CaddyServer.id)).scalar_subquery().label("server_count"),
                select(func.count(CaddyConfig.id)).scalar_subquery().label("config_count"),
                select(func.count(ApiKey.id)).scalar_subquery().label("api_key_count"),
                select(func.count(AuditLog.id)).scalar_subquery().label("audit_count"),
            )
        )
    ).one()
    return {
        "server_count": int(counts.server_count),
        "config_count": int(counts.config_count),
        "api_key_count": int(counts.api_key_count),
        "audit_count": int(counts.audit_count),
        "servers": await server_repository.list_all(session, limit=5),
        "recent_logs": await audit_log_repository.list_recent(session, limit=8),
    }


def _config_history_entry(action: str, actor: str, note: str) -> dict[str, str]:
    return {
        "action": action,
        "actor": actor,
        "note": note,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await get_session_user(request, session)
    if current_user is not None:
        return redirect_to("/")
    return render_template(request, "login.html", current_user=None)


@router.post("/login")
@limiter.limit("5/minute")
async def login_action(request: Request, session: AsyncSession = Depends(get_db_session)):
    form = await _validated_form(request)
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    next_path = str(form.get("next", "/")) or "/"
    logger.debug("Login attempt for username=%r password_len=%d", username, len(password))
    user = await auth_service.authenticate(session, username, password)
    if user is None:
        logger.debug("Authentication failed for username=%r", username)
        await _audit_commit_and_flash(
            session,
            request,
            action="login_failed",
            resource_type="user",
            details={"username": username},
            status_code=401,
            flashes=(("danger", "Invalid credentials."),),
        )
        return redirect_to("/login")
    request.session.clear()
    request.session["user_id"] = user.id
    await _audit_commit_and_flash(
        session,
        request,
        action="login_success",
        resource_type="user",
        resource_id=str(user.id),
        details={"username": user.username},
        status_code=200,
        actor=user,
        flashes=(("success", f"Welcome back, {user.username}."),),
    )
    return redirect_to(_safe_next(next_path))


@router.post("/logout")
async def logout_action(request: Request, session: AsyncSession = Depends(get_db_session)):
    await _validated_form(request)
    current_user = await get_session_user(request, session)
    request.session.clear()
    if current_user is not None:
        await _audit_commit_and_flash(
            session,
            request,
            action="logout",
            resource_type="user",
            resource_id=str(current_user.id),
            status_code=200,
            actor=current_user,
        )
    push_flash(request, "info", "You have been signed out.")
    return redirect_to("/login")


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    context = {"page_title": "Dashboard", **(await _load_dashboard_context(session))}
    return render_template(request, "dashboard.html", current_user=current_user, context=context)


@router.get("/servers", response_class=HTMLResponse)
async def servers_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    context = {
        "page_title": "Servers",
        "servers": await server_repository.list_all(session),
    }
    return render_template(request, "servers.html", current_user=current_user, context=context)


@router.post("/servers")
async def create_server(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    form = await _validated_form(request)
    name = str(form.get("name", "")).strip()
    api_url = str(form.get("api_url", "")).strip().rstrip("/")
    api_port = _parse_int(form.get("api_port"))
    if api_port is None or not 1 <= api_port <= 65535:
        push_flash(request, "danger", "API port must be a valid integer between 1 and 65535.")
        return redirect_to("/servers")
    admin_api_path = str(form.get("admin_api_path", "/config/")).strip() or "/config/"
    if not admin_api_path.startswith("/"):
        admin_api_path = f"/{admin_api_path}"
    active = form.get("active") == "on"
    description = str(form.get("description", "")).strip() or None
    tags = split_csv(str(form.get("tags", "")))
    probe = CaddyServer(
        name=name,
        api_url=api_url,
        api_port=api_port,
        admin_api_path=admin_api_path,
        active=active,
        description=description,
        tags=tags,
        status="unknown",
    )
    status = "offline"
    try:
        await caddy_service.test_connection(probe)
        caddy_service.mark_server_online(probe)
        status = probe.status
    except ValueError as exc:
        push_flash(request, "danger", str(exc))
        return redirect_to("/servers")
    except httpx.HTTPError:
        status = "offline"
    try:
        server = await server_repository.create(
            session,
            name=name,
            api_url=api_url,
            api_port=api_port,
            admin_api_path=admin_api_path,
            active=active,
            description=description,
            tags=tags,
            status=status,
        )
        if probe.last_pinged is not None:
            server.last_pinged = probe.last_pinged
        success_flash = (
            "success",
            f"Server '{name}' created and validated.",
        )
        warning_flash = (
            "warning",
            f"Server '{name}' created, but the Caddy API is currently unreachable.",
        )
        await _audit_commit_and_flash(
            session,
            request,
            action="server_created",
            resource_type="server",
            resource_id=str(server.id),
            details={"name": server.name, "status": status},
            status_code=201,
            actor=current_user,
            flashes=((success_flash if status == "online" else warning_flash),),
        )
    except IntegrityError:
        await session.rollback()
        push_flash(request, "danger", "A server with that name already exists.")
        return redirect_to("/servers")
    return redirect_to("/servers")


@router.post("/servers/{server_id}/test")
@limiter.limit("10/minute")
async def test_server(request: Request, server_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await _validated_form(request)
    del form
    server = await server_repository.get_by_id(session, server_id)
    if server is None:
        push_flash(request, "danger", "Server not found.")
        return redirect_to("/servers")
    try:
        await caddy_service.test_connection(server)
        caddy_service.mark_server_online(server)
        push_flash(request, "success", f"Connection to '{server.name}' is healthy.")
        status_code = 200
    except (httpx.HTTPError, ValueError) as exc:
        caddy_service.mark_server_offline(server)
        push_flash(request, "danger", f"Connection test failed: {exc}")
        status_code = 502
    await _audit_commit_and_flash(
        session,
        request,
        action="server_tested",
        resource_type="server",
        resource_id=str(server.id),
        details={"status": server.status},
        status_code=status_code,
        actor=current_user,
    )
    return redirect_to("/servers")


@router.post("/servers/{server_id}/sync")
@limiter.limit("10/minute")
async def sync_server_config(request: Request, server_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    await _validated_form(request)
    server = await server_repository.get_by_id(session, server_id)
    if server is None:
        push_flash(request, "danger", "Server not found.")
        return redirect_to("/servers")
    try:
        config_payload = await caddy_service.fetch_config(server)
        caddy_service.mark_server_online(server)
        config = await config_repository.create(
            session,
            name=f"{server.name} live snapshot",
            json_config=config_payload,
            status="draft",
            metadata_json={
                "source": "server_sync",
                "server_id": server.id,
                "sites": caddy_service.extract_sites(config_payload),
            },
            history_entries=[_config_history_entry("synced", current_user.username, f"Imported from server {server.name}.")],
            servers=[server],
        )
        await _audit_commit_and_flash(
            session,
            request,
            action="server_synced",
            resource_type="config",
            resource_id=str(config.id),
            details={"server": server.name},
            status_code=201,
            actor=current_user,
            flashes=(("success", f"Imported live configuration from '{server.name}'."),),
        )
    except (httpx.HTTPError, ValueError) as exc:
        caddy_service.mark_server_offline(server)
        await session.commit()
        push_flash(request, "danger", f"Could not pull the live configuration: {exc}")
    return redirect_to("/configs")


@router.post("/servers/{server_id}/delete")
async def delete_server(request: Request, server_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    await _validated_form(request)
    server = await server_repository.get_by_id(session, server_id)
    if server is None:
        push_flash(request, "danger", "Server not found.")
        return redirect_to("/servers")
    server_name = server.name
    await server_repository.delete(session, server)
    await _audit_commit_and_flash(
        session,
        request,
        action="server_deleted",
        resource_type="server",
        resource_id=str(server_id),
        details={"name": server_name},
        status_code=200,
        actor=current_user,
        flashes=(("info", f"Server '{server_name}' has been removed."),),
    )
    return redirect_to("/servers")


@router.get("/configs", response_class=HTMLResponse)
@router.get("/configs/{config_id}", response_class=HTMLResponse)
async def configs_page(request: Request, config_id: int | None = None, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    configs = await config_repository.list_all(session)
    selected_config = await config_repository.get_by_id(session, config_id) if config_id else None
    context = {
        "page_title": "Configurations",
        "configs": configs,
        "servers": await server_repository.list_all(session),
        "selected_config": selected_config,
        "selected_server_ids": [server.id for server in selected_config.servers] if selected_config else [],
        "selected_config_json": pretty_json(selected_config.json_config) if selected_config else pretty_json({}),
        "selected_metadata_json": pretty_json(selected_config.metadata_json) if selected_config else pretty_json({}),
    }
    return render_template(request, "configs.html", current_user=current_user, context=context)


@router.post("/configs")
async def save_config(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    form = await _validated_form(request)
    config_id_raw = str(form.get("config_id", "")).strip()
    name = str(form.get("name", "")).strip()
    status = str(form.get("status", "draft")).strip() or "draft"
    json_config = parse_json_object(str(form.get("json_config", "{}")), "Configuration")
    metadata_json = parse_json_object(str(form.get("metadata_json", "{}")), "Metadata")
    selected_ids: set[int] = set()
    for raw_server_id in form.getlist("servers"):
        parsed_server_id = _parse_int(raw_server_id)
        if parsed_server_id is None:
            push_flash(request, "danger", "One or more selected servers are invalid.")
            return redirect_to("/configs")
        selected_ids.add(parsed_server_id)
    all_servers = await server_repository.list_all(session)
    selected_servers = [server for server in all_servers if server.id in selected_ids]
    if config_id_raw:
        config_id = _parse_int(config_id_raw)
        if config_id is None:
            push_flash(request, "danger", "Configuration identifier is invalid.")
            return redirect_to("/configs")
        config = await config_repository.get_by_id(session, config_id)
        if config is None:
            push_flash(request, "danger", "Configuration not found.")
            return redirect_to("/configs")
        history_entries = list(config.history_entries)
        history_entries.append(_config_history_entry("updated", current_user.username, "Configuration edited from the UI."))
        await config_repository.update(
            session,
            config,
            name=name,
            json_config=json_config,
            status=status,
            metadata_json=metadata_json,
            history_entries=history_entries,
            servers=selected_servers,
        )
        action = "config_updated"
        resource_id = str(config.id)
        flash_message = f"Configuration '{config.name}' updated."
    else:
        config = await config_repository.create(
            session,
            name=name,
            json_config=json_config,
            status=status,
            metadata_json=metadata_json,
            history_entries=[_config_history_entry("created", current_user.username, "Configuration created from the UI.")],
            servers=selected_servers,
        )
        action = "config_created"
        resource_id = str(config.id)
        flash_message = f"Configuration '{config.name}' created."
    await _audit_commit_and_flash(
        session,
        request,
        action=action,
        resource_type="config",
        resource_id=resource_id,
        details={"name": name, "server_ids": sorted(selected_ids)},
        status_code=200,
        actor=current_user,
        flashes=(("success", flash_message),),
    )
    return redirect_to(f"/configs/{resource_id}")


@router.post("/configs/{config_id}/deploy")
@limiter.limit("10/minute")
async def deploy_config(request: Request, config_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    await _validated_form(request)
    config = await config_repository.get_by_id(session, config_id)
    if config is None:
        push_flash(request, "danger", "Configuration not found.")
        return redirect_to("/configs")
    if not config.servers:
        push_flash(request, "warning", "Assign at least one server before deploying a configuration.")
        return redirect_to(f"/configs/{config_id}")
    deployed_count = 0
    errors: list[str] = []
    for server in config.servers:
        if not server.active:
            errors.append(f"{server.name} is inactive")
            continue
        try:
            await caddy_service.deploy_config(server, config.json_config)
            caddy_service.mark_server_online(server)
            server.active_config_id = config.id
            deployed_count += 1
        except (httpx.HTTPError, ValueError) as exc:
            caddy_service.mark_server_offline(server)
            errors.append(f"{server.name}: {exc}")
    config.status = "live" if deployed_count > 0 else config.status
    config.history_entries = list(config.history_entries) + [
        _config_history_entry(
            "deployed",
            current_user.username,
            f"Deployment finished. Success: {deployed_count}, failed: {len(errors)}.",
        )
    ]
    flashes: list[tuple[str, str]] = []
    if deployed_count:
        flashes.append(("success", f"Configuration deployed to {deployed_count} server(s)."))
    if errors:
        flashes.append(("warning", "Some deploy targets failed: " + "; ".join(errors)))
    await _audit_commit_and_flash(
        session,
        request,
        action="config_deployed",
        resource_type="config",
        resource_id=str(config.id),
        details={"deployed": deployed_count, "errors": errors},
        status_code=200 if not errors else 207,
        actor=current_user,
        flashes=tuple(flashes),
    )
    return redirect_to(f"/configs/{config.id}")


@router.post("/configs/{config_id}/delete")
async def delete_config(request: Request, config_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    await _validated_form(request)
    config = await config_repository.get_by_id(session, config_id)
    if config is None:
        push_flash(request, "danger", "Configuration not found.")
        return redirect_to("/configs")
    config_name = config.name
    await config_repository.delete(session, config)
    await _audit_commit_and_flash(
        session,
        request,
        action="config_deleted",
        resource_type="config",
        resource_id=str(config_id),
        details={"name": config_name},
        status_code=200,
        actor=current_user,
        flashes=(("info", f"Configuration '{config_name}' deleted."),),
    )
    return redirect_to("/configs")


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    context = {"page_title": "Profile"}
    return render_template(request, "profile.html", current_user=current_user, context=context)


@router.post("/profile")
async def update_profile(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await _validated_form(request)
    username = str(form.get("username", "")).strip()
    email = str(form.get("email", "")).strip() or None
    existing_username = await user_repository.get_by_username(session, username)
    if existing_username is not None and existing_username.id != current_user.id:
        push_flash(request, "danger", "That username is already in use.")
        return redirect_to("/profile")
    if email:
        existing_email = await user_repository.get_by_email(session, email)
        if existing_email is not None and existing_email.id != current_user.id:
            push_flash(request, "danger", "That email address is already in use.")
            return redirect_to("/profile")
    try:
        await user_repository.update_profile(session, current_user, username=username, email=email)
        await _audit_commit_and_flash(
            session,
            request,
            action="profile_updated",
            resource_type="user",
            resource_id=str(current_user.id),
            details={"username": username, "email": email},
            status_code=200,
            actor=current_user,
            flashes=(("success", "Profile updated."),),
        )
    except IntegrityError:
        await session.rollback()
        push_flash(request, "danger", "That username or email address is already in use.")
        return redirect_to("/profile")
    return redirect_to("/profile")


@router.post("/profile/password")
@limiter.limit("5/minute")
async def change_password(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await _validated_form(request)
    current_password = str(form.get("current_password", ""))
    new_password = str(form.get("new_password", ""))
    confirm_password = str(form.get("confirm_password", ""))
    if new_password != confirm_password:
        push_flash(request, "danger", "The new passwords do not match.")
        return redirect_to("/profile")
    if not await auth_service.verify_password(current_password, current_user.password_hash):
        push_flash(request, "danger", "Your current password is incorrect.")
        return redirect_to("/profile")
    try:
        await auth_service.update_password(session, current_user, new_password)
    except WeakPasswordError as exc:
        push_flash(request, "danger", str(exc))
        return redirect_to("/profile")
    await _audit_commit_and_flash(
        session,
        request,
        action="password_changed",
        resource_type="user",
        resource_id=str(current_user.id),
        status_code=200,
        actor=current_user,
        flashes=(("success", "Password updated."),),
    )
    request.session.clear()
    request.session["user_id"] = current_user.id
    return redirect_to("/profile")


@router.get("/users", response_class=HTMLResponse)
async def users_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    context = {
        "page_title": "Users",
        "users": await user_repository.list_all(session),
    }
    return render_template(request, "users.html", current_user=current_user, context=context)


@router.post("/users")
@limiter.limit("10/minute")
async def create_user(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    form = await _validated_form(request)
    username = str(form.get("username", "")).strip()
    email = str(form.get("email", "")).strip() or None
    password = str(form.get("password", ""))
    role = str(form.get("role", "user")).strip() or "user"
    if await user_repository.get_by_username(session, username):
        push_flash(request, "danger", "That username already exists.")
        return redirect_to("/users")
    if email and await user_repository.get_by_email(session, email):
        push_flash(request, "danger", "That email address already exists.")
        return redirect_to("/users")
    try:
        created_user = await auth_service.create_user(
            session,
            username=username,
            email=email,
            password=password,
            role=role,
        )
    except WeakPasswordError as exc:
        push_flash(request, "danger", str(exc))
        return redirect_to("/users")
    try:
        await _audit_commit_and_flash(
            session,
            request,
            action="user_created",
            resource_type="user",
            resource_id=str(created_user.id),
            details={"username": created_user.username, "role": created_user.role},
            status_code=201,
            actor=current_user,
            flashes=(("success", f"User '{created_user.username}' created."),),
        )
    except IntegrityError:
        await session.rollback()
        push_flash(request, "danger", "That username or email address already exists.")
        return redirect_to("/users")
    return redirect_to("/users")


@router.get("/api-keys", response_class=HTMLResponse)
async def api_keys_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    return await _render_api_keys_page(request, session, current_user)


@router.post("/api-keys")
@limiter.limit("10/minute")
async def create_api_key(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await _validated_form(request)
    name = str(form.get("name", "")).strip()
    permissions = {
        "read": form.get("perm_read") == "on",
        "write": form.get("perm_write") == "on",
        "delete": form.get("perm_delete") == "on",
    }
    expires_at = parse_expires_days(str(form.get("expires_days", "")).strip() or None)
    api_key, raw_key = await auth_service.create_api_key(
        session,
        user_id=current_user.id,
        name=name,
        permissions=permissions,
        expires_at=expires_at,
    )
    await _audit_commit_and_flash(
        session,
        request,
        action="api_key_created",
        resource_type="api_key",
        resource_id=str(api_key.id),
        details={"name": api_key.name, "permissions": permissions},
        status_code=201,
        actor=current_user,
        flashes=(("success", "API key created. Copy it now. It will not be shown again."),),
    )
    return await _render_api_keys_page(
        request,
        session,
        current_user,
        pending_api_key=raw_key,
        status_code=201,
    )


@router.post("/api-keys/{api_key_id}/toggle")
async def toggle_api_key(request: Request, api_key_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    await _validated_form(request)
    api_key = await api_key_repository.get_by_id(session, api_key_id)
    if api_key is None:
        push_flash(request, "danger", "API key not found.")
        return redirect_to("/api-keys")
    if current_user.role != "admin" and api_key.user_id != current_user.id:
        push_flash(request, "danger", "You cannot modify that API key.")
        return redirect_to("/api-keys")
    is_active = not api_key.is_active
    await api_key_repository.set_active(session, api_key, is_active)
    await _audit_commit_and_flash(
        session,
        request,
        action="api_key_toggled",
        resource_type="api_key",
        resource_id=str(api_key.id),
        details={"active": is_active},
        status_code=200,
        actor=current_user,
        flashes=(("success", f"API key {'enabled' if is_active else 'disabled'}."),),
    )
    return redirect_to("/api-keys")


@router.get("/audit-logs", response_class=HTMLResponse)
async def audit_logs_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await _require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    logs = await audit_log_repository.list_recent(session, limit=500)
    context = {
        "page_title": "Audit Logs",
        "logs": logs,
    }
    return render_template(request, "audit_logs.html", current_user=current_user, context=context)
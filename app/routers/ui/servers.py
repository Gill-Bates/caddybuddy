#!/usr/bin/env python3
#
# app/routers/ui/servers.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from urllib.parse import urlsplit

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.models.entities import CaddyConfig, CaddyServer
from app.repositories.configs import config_repository
from app.repositories.config_templates import TemplateAlreadyExistsError, config_template_repository
from app.repositories.deployments import deployment_repository
from app.repositories.servers import server_repository
from app.repositories.sites import site_repository
from app.services.caddy import CaddyServiceError, caddy_service
from app.services.events import publish_resource_event
from app.utils.parsing import split_csv

from ._common import (
    audit_commit_and_flash,
    config_history_entry,
    parse_int,
    require_admin,
    require_user,
    validated_form,
)

router = APIRouter()

_SERVER_NAME_MAX_LENGTH = 120
_SERVER_API_URL_MAX_LENGTH = 255
_SERVER_ADMIN_API_PATH_MAX_LENGTH = 120


async def _resolve_import_template(
    session: AsyncSession,
    *,
    template_name: str,
    template_description: str,
    caddyfile: str,
    actor_username: str,
):
    template = await config_template_repository.get_by_name(session, template_name)
    matching_template = await config_template_repository.get_by_caddyfile(session, caddyfile)

    if matching_template is not None and (template is None or matching_template.id != template.id):
        return matching_template

    if template is None:
        try:
            async with session.begin_nested():
                return await config_template_repository.create(
                    session,
                    name=template_name,
                    description=template_description,
                    caddyfile=caddyfile,
                    created_by=actor_username,
                )
        except TemplateAlreadyExistsError:
            # The savepoint was rolled back; the outer transaction is still usable.
            matching = await config_template_repository.get_by_caddyfile(session, caddyfile)
            if matching is not None:
                return matching
            raise

    try:
        async with session.begin_nested():
            return await config_template_repository.update(
                session,
                template,
                description=template_description,
                caddyfile=caddyfile,
                change_summary=f"Synced from server {template_name}",
                updated_by=actor_username,
            )
    except TemplateAlreadyExistsError:
        # The savepoint was rolled back; the outer transaction is still usable.
        matching = await config_template_repository.get_by_caddyfile(session, caddyfile)
        if matching is not None:
            return matching
        raise


@dataclass(slots=True)
class _ServerProbe:
    name: str
    api_url: str
    api_port: int
    admin_api_path: str
    active: bool
    tags: list[str]
    status: str = "unknown"
    last_pinged: datetime | None = None


async def _import_live_config(
    session: AsyncSession,
    *,
    server: CaddyServer,
    config_payload: dict,
    actor_username: str,
) -> tuple[CaddyConfig, list[int], list[str]]:
    imported_site_ids: list[int] = []
    skipped_domains: list[str] = []
    imported_sites = caddy_service.extract_site_definitions(
        config_payload,
        template_name_prefix=server.name,
    )

    # Priority 2: Cap bulk imports to avoid long-running transactions
    _MAX_IMPORT_SITES = 500
    if len(imported_sites) > _MAX_IMPORT_SITES:
        imported_sites = imported_sites[:_MAX_IMPORT_SITES]

    for imported_site in imported_sites:
        template_description = f"Imported from server '{server.name}'."
        template = await _resolve_import_template(
            session,
            template_name=imported_site.template_name,
            template_description=template_description,
            caddyfile=imported_site.caddyfile,
            actor_username=actor_username,
        )

        existing_site = await site_repository.get_by_domain(session, imported_site.domain)
        if existing_site is not None:
            skipped_domains.append(imported_site.domain)
            continue

        created_site = await site_repository.create(
            session,
            domain=imported_site.domain,
            config_template_id=template.id,
            enabled=True,
            description=template_description,
            variables={"upstream": imported_site.upstream} if imported_site.upstream else {},
            ssl_enabled=imported_site.ssl_enabled,
            ssl_provider="letsencrypt" if imported_site.ssl_enabled else "none",
        )
        imported_site_ids.append(created_site.id)

        # Mark the site as already deployed on this server so it does not
        # appear in the deployment queue.
        await deployment_repository.create_imported(
            session,
            site_id=created_site.id,
            server_id=server.id,
            rendered_config=imported_site.caddyfile,
            deployed_by=actor_username,
        )

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
        history_entries=[config_history_entry("synced", actor_username, f"Imported from server {server.name}.")],
        servers=[server],
    )
    await server_repository.update(session, server, active_config_id=config.id)
    return config, imported_site_ids, skipped_domains


@router.get("/servers", response_class=HTMLResponse)
async def servers_page(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    context = {
        "page_title": "Servers",
        "servers": await server_repository.list_all(session),
    }
    return render_template(request, "servers.html", current_user=current_user, context=context)


@router.post("/servers")
@limiter.limit("5/minute")
async def create_server(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    form = await validated_form(request)
    name = str(form.get("name", "")).strip()
    if not name or len(name) > _SERVER_NAME_MAX_LENGTH:
        push_flash(
            request,
            "danger",
            f"Server name must be between 1 and {_SERVER_NAME_MAX_LENGTH} characters.",
        )
        return redirect_to("/servers")
    api_url = str(form.get("api_url", "")).strip().rstrip("/")
    if not api_url or len(api_url) > _SERVER_API_URL_MAX_LENGTH:
        push_flash(
            request,
            "danger",
            f"API URL must be between 1 and {_SERVER_API_URL_MAX_LENGTH} characters.",
        )
        return redirect_to("/servers")

    # Finding 1: Strict URL validation to prevent SSRF
    try:
        parsed = urlsplit(api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
    except Exception:
        push_flash(request, "danger", "API URL must be a valid HTTP/HTTPS URL with a host.")
        return redirect_to("/servers")
    api_port = parse_int(form.get("api_port"))
    if api_port is None or not 1 <= api_port <= 65535:
        push_flash(request, "danger", "API port must be a valid integer between 1 and 65535.")
        return redirect_to("/servers")
    admin_api_path = str(form.get("admin_api_path", "/config/")).strip() or "/config/"
    if not admin_api_path.startswith("/"):
        admin_api_path = f"/{admin_api_path}"
    if len(admin_api_path) > _SERVER_ADMIN_API_PATH_MAX_LENGTH:
        push_flash(
            request,
            "danger",
            f"Admin API path must not exceed {_SERVER_ADMIN_API_PATH_MAX_LENGTH} characters.",
        )
        return redirect_to("/servers")
    active = form.get("active") == "on"
    tags = split_csv(str(form.get("tags", "")))
    probe = _ServerProbe(
        name=name,
        api_url=api_url,
        api_port=api_port,
        admin_api_path=admin_api_path,
        active=active,
        tags=tags,
        status="unknown",
    )
    status = "offline"
    config_payload: dict | None = None
    try:
        config_payload = await caddy_service.test_connection(probe)
        caddy_service.mark_server_online(probe)
        status = probe.status
    except ValueError as exc:
        push_flash(request, "danger", str(exc))
        return redirect_to("/servers")
    except CaddyServiceError:
        status = "offline"
    try:
        server = await server_repository.create(
            session,
            name=name,
            api_url=api_url,
            api_port=api_port,
            admin_api_path=admin_api_path,
            active=active,
            tags=tags,
            status=status,
        )
    except IntegrityError:
        await session.rollback()
        push_flash(request, "danger", "A server with that name already exists.")
        return redirect_to("/servers")

    try:
        if probe.last_pinged is not None:
            server.last_pinged = probe.last_pinged
        imported_site_ids: list[int] = []
        skipped_domains: list[str] = []
        if status == "online" and config_payload is not None:
            # Finding 4: Execute import inside a savepoint to preserve server record on failure
            try:
                async with session.begin_nested():
                    _, imported_site_ids, skipped_domains = await _import_live_config(
                        session,
                        server=server,
                        config_payload=config_payload,
                        actor_username=current_user.username,
                    )
            except (IntegrityError, TemplateAlreadyExistsError):
                push_flash(
                    request,
                    "warning",
                    "A site or Caddyfile conflict occurred during import; server was created without sites.",
                )

        success_msg = f"Server '{name}' created and imported {len(imported_site_ids)} site(s)."
        if skipped_domains:
            success_msg += f" {len(skipped_domains)} existing site(s) were skipped."

        success_flash = ("success", success_msg)
        warning_flash = (
            "warning",
            f"Server '{name}' created, but the Caddy API is currently unreachable.",
        )
        await audit_commit_and_flash(
            session,
            request,
            action="server_created",
            resource_type="server",
            resource_id=str(server.id),
            details={"name": server.name, "status": status, "imported_sites": len(imported_site_ids)},
            status_code=201,
            actor=current_user,
            flashes=((success_flash if status == "online" else warning_flash),),
        )
        await publish_resource_event("server", "created", str(server.id))
        for site_id in imported_site_ids:
            await publish_resource_event("site", "created", str(site_id))
    except (IntegrityError, TemplateAlreadyExistsError):
        await session.rollback()
        push_flash(request, "danger", "A server with that name already exists.")
        return redirect_to("/servers")
    if status == "online":
        return redirect_to(f"/templates?server_id={server.id}")
    return redirect_to("/servers")


@router.post("/servers/{server_id}/test")
@limiter.limit("10/minute")
async def test_server(request: Request, server_id: int, session: AsyncSession = Depends(get_db_session)):
    # Finding 2: Restrict state-mutating connection tests to admins
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    await validated_form(request)
    server = await server_repository.get_by_id(session, server_id)
    if server is None:
        push_flash(request, "danger", "Server not found.")
        return redirect_to("/servers")
    try:
        await caddy_service.test_connection(server)
        caddy_service.mark_server_online(server)
        push_flash(request, "success", f"Connection to '{server.name}' is healthy.")
        status_code = 200
    except (CaddyServiceError, ValueError) as exc:
        caddy_service.mark_server_offline(server)
        push_flash(request, "danger", f"Connection test failed: {exc}")
        status_code = 502
    await audit_commit_and_flash(
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
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    await validated_form(request)
    server = await server_repository.get_by_id(session, server_id)
    if server is None:
        push_flash(request, "danger", "Server not found.")
        return redirect_to("/servers")
    try:
        config_payload = await caddy_service.fetch_config(server)
        caddy_service.mark_server_online(server)
        config, imported_site_ids, skipped_domains = await _import_live_config(
            session,
            server=server,
            config_payload=config_payload,
            actor_username=current_user.username,
        )
        sync_msg = f"Imported live configuration from '{server.name}' and discovered {len(imported_site_ids)} site(s)."
        if skipped_domains:
            sync_msg += f" {len(skipped_domains)} existing site(s) were skipped."

        await audit_commit_and_flash(
            session,
            request,
            action="server_synced",
            resource_type="config",
            resource_id=str(config.id),
            details={
                "server": server.name,
                "imported_sites": len(imported_site_ids),
                "skipped_sites": len(skipped_domains),
            },
            status_code=201,
            actor=current_user,
            flashes=(("success", sync_msg),),
        )
        for site_id in imported_site_ids:
            await publish_resource_event("site", "created", str(site_id))
    except (IntegrityError, TemplateAlreadyExistsError):
        await session.rollback()
        push_flash(request, "danger", "A site or Caddyfile conflict occurred during import.")
        return redirect_to(f"/templates?server_id={server.id}")
    except (CaddyServiceError, ValueError) as exc:
        await session.rollback()
        caddy_service.mark_server_offline(server)
        await server_repository.update(
            session,
            server,
            status=server.status,
            last_pinged=server.last_pinged,
        )
        await session.commit()
        push_flash(request, "danger", f"Could not pull the live configuration: {exc}")
    return redirect_to(f"/templates?server_id={server.id}")


@router.post("/servers/{server_id}/delete")
@limiter.limit("10/minute")
async def delete_server(request: Request, server_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    await validated_form(request)
    server = await server_repository.get_by_id(session, server_id)
    if server is None:
        push_flash(request, "danger", "Server not found.")
        return redirect_to("/servers")
    server_name = server.name
    try:
        await server_repository.delete(session, server)
    except IntegrityError:
        # Finding 3: Handle foreign key constraints gracefully
        await session.rollback()
        push_flash(request, "danger", f"Cannot delete server '{server_name}': it is still referenced by existing deployments or sites.")
        return redirect_to("/servers")
    await audit_commit_and_flash(
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
    await publish_resource_event("server", "deleted", str(server_id))
    return redirect_to("/servers")

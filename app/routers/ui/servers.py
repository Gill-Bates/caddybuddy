#!/usr/bin/env python3
#
# app/routers/ui/servers.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.models.entities import CaddyServer
from app.repositories.configs import config_repository
from app.repositories.servers import server_repository
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
    probe = CaddyServer(
        name=name,
        api_url=api_url,
        api_port=api_port,
        admin_api_path=admin_api_path,
        active=active,
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
        await audit_commit_and_flash(
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
        await publish_resource_event("server", "created", str(server.id))
    except IntegrityError:
        await session.rollback()
        push_flash(request, "danger", "A server with that name already exists.")
        return redirect_to("/servers")
    return redirect_to("/servers")


@router.post("/servers/{server_id}/test")
@limiter.limit("10/minute")
async def test_server(request: Request, server_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await validated_form(request)
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
            history_entries=[config_history_entry("synced", current_user.username, f"Imported from server {server.name}.")],
            servers=[server],
        )
        await audit_commit_and_flash(
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
    except (CaddyServiceError, ValueError) as exc:
        caddy_service.mark_server_offline(server)
        await session.commit()
        push_flash(request, "danger", f"Could not pull the live configuration: {exc}")
    return redirect_to("/configs")


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
    await server_repository.delete(session, server)
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

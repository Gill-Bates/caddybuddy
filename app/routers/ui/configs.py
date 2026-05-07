#!/usr/bin/env python3
#
# app/routers/ui/configs.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

# Configuration management routes.
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.limiter import limiter
from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.configs import config_repository
from app.repositories.servers import server_repository
from app.services.caddy import caddy_service
from app.services.events import publish_resource_event
from app.utils.parsing import parse_json_object, pretty_json

from ._common import (
    audit_commit_and_flash,
    config_history_entry,
    parse_int,
    require_admin,
    require_user,
    validated_form,
)

router = APIRouter()


@router.get("/configs", response_class=HTMLResponse)
@router.get("/configs/{config_id}", response_class=HTMLResponse)
async def configs_page(request: Request, config_id: int | None = None, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_user(request, session)
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
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    form = await validated_form(request)
    config_id_raw = str(form.get("config_id", "")).strip()
    name = str(form.get("name", "")).strip()
    status = str(form.get("status", "draft")).strip() or "draft"
    json_config = parse_json_object(str(form.get("json_config", "{}")), "Configuration")
    metadata_json = parse_json_object(str(form.get("metadata_json", "{}")), "Metadata")
    selected_ids: set[int] = set()
    for raw_server_id in form.getlist("servers"):
        parsed_server_id = parse_int(raw_server_id)
        if parsed_server_id is None:
            push_flash(request, "danger", "One or more selected servers are invalid.")
            return redirect_to("/configs")
        selected_ids.add(parsed_server_id)
    all_servers = await server_repository.list_all(session)
    selected_servers = [server for server in all_servers if server.id in selected_ids]
    if config_id_raw:
        config_id = parse_int(config_id_raw)
        if config_id is None:
            push_flash(request, "danger", "Configuration identifier is invalid.")
            return redirect_to("/configs")
        config = await config_repository.get_by_id(session, config_id)
        if config is None:
            push_flash(request, "danger", "Configuration not found.")
            return redirect_to("/configs")
        history_entries = list(config.history_entries)
        history_entries.append(config_history_entry("updated", current_user.username, "Configuration edited from the UI."))
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
            history_entries=[config_history_entry("created", current_user.username, "Configuration created from the UI.")],
            servers=selected_servers,
        )
        action = "config_created"
        resource_id = str(config.id)
        flash_message = f"Configuration '{config.name}' created."
    await audit_commit_and_flash(
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
    event_action = "updated" if action == "config_updated" else "created"
    await publish_resource_event("config", event_action, resource_id)
    return redirect_to(f"/configs/{resource_id}")


@router.post("/configs/{config_id}/deploy")
@limiter.limit("10/minute")
async def deploy_config(request: Request, config_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    await validated_form(request)
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
        config_history_entry(
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
    await audit_commit_and_flash(
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
    await publish_resource_event("config", "deployed", str(config.id))
    return redirect_to(f"/configs/{config.id}")


@router.post("/configs/{config_id}/delete")
async def delete_config(request: Request, config_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    await validated_form(request)
    config = await config_repository.get_by_id(session, config_id)
    if config is None:
        push_flash(request, "danger", "Configuration not found.")
        return redirect_to("/configs")
    config_name = config.name
    await config_repository.delete(session, config)
    await audit_commit_and_flash(
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
    await publish_resource_event("config", "deleted", str(config_id))
    return redirect_to("/configs")

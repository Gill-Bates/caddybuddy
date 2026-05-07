#!/usr/bin/env python3
#
# app/routers/ui/domains.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.database.session import get_db_session
from app.dependencies.web import push_flash, redirect_to, render_template
from app.repositories.domains import domain_repository
from app.repositories.servers import server_repository
from app.services.events import publish_resource_event
from app.utils.caddyfile import (
    CADDY_DIRECTIVES_EXAMPLE,
    build_domain_site_preview,
    parse_domain_directive_form_state,
    prepare_domain_directives,
)

from ._common import (
    audit_commit_and_flash,
    parse_int,
    require_admin,
    require_user,
    validated_form,
)

router = APIRouter()


@dataclass(slots=True)
class DomainFormInput:
    domain_id_raw: str
    name: str
    server_id: int | None
    upstream: str | None
    reverse_proxy_options: str
    encode_directives: str
    header_directives: str
    request_body_directives: str
    log_directives: str
    tls_directives: str
    basic_auth_directives: str
    custom_directives: str
    ssl_enabled: bool
    ssl_provider: str
    active: bool
    description: str | None


def _read_domain_form_input(form: FormData) -> DomainFormInput:
    return DomainFormInput(
        domain_id_raw=str(form.get("domain_id", "")).strip(),
        name=str(form.get("name", "")).strip().lower(),
        server_id=parse_int(form.get("server_id")) if form.get("server_id") else None,
        upstream=str(form.get("upstream", "")).strip() or None,
        reverse_proxy_options=str(form.get("reverse_proxy_options", "")).strip(),
        encode_directives=str(form.get("encode_directives", "")).strip(),
        header_directives=str(form.get("header_directives", "")).strip(),
        request_body_directives=str(form.get("request_body_directives", "")).strip(),
        log_directives=str(form.get("log_directives", "")).strip(),
        tls_directives=str(form.get("tls_directives", "")).strip(),
        basic_auth_directives=str(form.get("basic_auth_directives", "")).strip(),
        custom_directives=str(form.get("caddy_directives", "")).strip(),
        ssl_enabled=form.get("ssl_enabled") == "on",
        ssl_provider=str(form.get("ssl_provider", "letsencrypt")).strip() or "letsencrypt",
        active=form.get("active") == "on",
        description=str(form.get("description", "")).strip() or None,
    )


def _domain_redirect_path(domain_id_raw: str) -> str:
    return f"/domains/{domain_id_raw}" if domain_id_raw else "/domains"


def _directive_form_state(selected_domain):
    if selected_domain is None:
        return parse_domain_directive_form_state(None)

    return parse_domain_directive_form_state(
        selected_domain.caddy_directives,
        upstream_fallback=selected_domain.upstream,
    )


def _preview_for_domain(selected_domain) -> str:
    if selected_domain is None:
        return build_domain_site_preview(
            name="example.com",
            upstream="http://backend-service:3000",
            caddy_directives=CADDY_DIRECTIVES_EXAMPLE,
            ssl_enabled=True,
        )

    return build_domain_site_preview(
        name=selected_domain.name,
        upstream=selected_domain.upstream,
        caddy_directives=selected_domain.caddy_directives,
        ssl_enabled=selected_domain.ssl_enabled,
    )


@router.get("/domains", response_class=HTMLResponse)
@router.get("/domains/{domain_id}", response_class=HTMLResponse)
async def domains_page(request: Request, domain_id: int | None = None, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_user(request, session)
    if current_user is None:
        return redirect_to("/login")
    domains = await domain_repository.list_all(session)
    selected_domain = await domain_repository.get_by_id(session, domain_id) if domain_id else None
    directive_form = _directive_form_state(selected_domain)
    context = {
        "page_title": "Domains",
        "domains": domains,
        "servers": await server_repository.list_all(session),
        "selected_domain": selected_domain,
        "domain_directive_form": directive_form,
        "domain_caddy_preview": _preview_for_domain(selected_domain),
        "caddy_directives_example": CADDY_DIRECTIVES_EXAMPLE,
        "ssl_providers": ["letsencrypt", "zerossl", "manual", "none"],
    }
    return render_template(request, "domains.html", current_user=current_user, context=context)


@router.post("/domains/preview")
async def preview_domain(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_user(request, session)
    if current_user is None:
        return JSONResponse({"detail": "Authentication required."}, status_code=401)

    form = await validated_form(request)
    form_input = _read_domain_form_input(form)
    directive_result = prepare_domain_directives(
        upstream=form_input.upstream,
        reverse_proxy_options=form_input.reverse_proxy_options,
        encode_directives=form_input.encode_directives,
        header_directives=form_input.header_directives,
        request_body_directives=form_input.request_body_directives,
        log_directives=form_input.log_directives,
        tls_directives=form_input.tls_directives,
        basic_auth_directives=form_input.basic_auth_directives,
        custom_directives=form_input.custom_directives,
    )
    preview = build_domain_site_preview(
        name=form_input.name or "example.com",
        upstream=directive_result.upstream,
        caddy_directives=directive_result.caddy_directives,
        ssl_enabled=form_input.ssl_enabled,
    )
    return JSONResponse({"preview": preview, "errors": list(directive_result.errors)})


@router.post("/domains")
async def save_domain(request: Request, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    form = await validated_form(request)
    form_input = _read_domain_form_input(form)
    directive_result = prepare_domain_directives(
        upstream=form_input.upstream,
        reverse_proxy_options=form_input.reverse_proxy_options,
        encode_directives=form_input.encode_directives,
        header_directives=form_input.header_directives,
        request_body_directives=form_input.request_body_directives,
        log_directives=form_input.log_directives,
        tls_directives=form_input.tls_directives,
        basic_auth_directives=form_input.basic_auth_directives,
        custom_directives=form_input.custom_directives,
    )
    if directive_result.errors:
        for error in directive_result.errors:
            push_flash(request, "danger", error)
        return redirect_to(_domain_redirect_path(form_input.domain_id_raw))

    name = form_input.name
    upstream = directive_result.upstream
    caddy_directives = directive_result.caddy_directives

    if not name:
        push_flash(request, "danger", "Domain name is required.")
        return redirect_to("/domains")

    if form_input.domain_id_raw:
        domain_id = parse_int(form_input.domain_id_raw)
        if domain_id is None:
            push_flash(request, "danger", "Domain identifier is invalid.")
            return redirect_to("/domains")
        domain = await domain_repository.get_by_id(session, domain_id)
        if domain is None:
            push_flash(request, "danger", "Domain not found.")
            return redirect_to("/domains")
        existing = await domain_repository.get_by_name(session, name)
        if existing is not None and existing.id != domain.id:
            push_flash(request, "danger", "A domain with that name already exists.")
            return redirect_to(f"/domains/{domain_id}")
        await domain_repository.update(
            session,
            domain,
            name=name,
            server_id=form_input.server_id,
            upstream=upstream,
            caddy_directives=caddy_directives,
            ssl_enabled=form_input.ssl_enabled,
            ssl_provider=form_input.ssl_provider,
            active=form_input.active,
            description=form_input.description,
        )
        action = "domain_updated"
        resource_id = str(domain.id)
        flash_message = f"Domain '{domain.name}' updated."
    else:
        if await domain_repository.get_by_name(session, name):
            push_flash(request, "danger", "A domain with that name already exists.")
            return redirect_to("/domains")
        try:
            domain = await domain_repository.create(
                session,
                name=name,
                server_id=form_input.server_id,
                upstream=upstream,
                caddy_directives=caddy_directives,
                ssl_enabled=form_input.ssl_enabled,
                ssl_provider=form_input.ssl_provider,
                active=form_input.active,
                description=form_input.description,
            )
        except IntegrityError:
            await session.rollback()
            push_flash(request, "danger", "A domain with that name already exists.")
            return redirect_to("/domains")
        action = "domain_created"
        resource_id = str(domain.id)
        flash_message = f"Domain '{domain.name}' created."
    await audit_commit_and_flash(
        session,
        request,
        action=action,
        resource_type="domain",
        resource_id=resource_id,
        details={
            "name": name,
            "server_id": form_input.server_id,
            "has_custom_directives": caddy_directives is not None,
            "upstream": upstream,
            "has_basic_auth_block": bool(form_input.basic_auth_directives),
            "has_encode_directive": bool(form_input.encode_directives),
            "has_header_block": bool(form_input.header_directives),
            "has_log_block": bool(form_input.log_directives),
            "has_request_body_block": bool(form_input.request_body_directives),
            "has_reverse_proxy_options": bool(form_input.reverse_proxy_options),
            "has_tls_block": bool(form_input.tls_directives),
        },
        status_code=200,
        actor=current_user,
        flashes=(("success", flash_message),),
    )
    domain_event_action = "updated" if action == "domain_updated" else "created"
    await publish_resource_event("domain", domain_event_action, resource_id)
    return redirect_to(f"/domains/{resource_id}")


@router.post("/domains/{domain_id}/delete")
async def delete_domain(request: Request, domain_id: int, session: AsyncSession = Depends(get_db_session)):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/")
    await validated_form(request)
    domain = await domain_repository.get_by_id(session, domain_id)
    if domain is None:
        push_flash(request, "danger", "Domain not found.")
        return redirect_to("/domains")
    domain_name = domain.name
    await domain_repository.delete(session, domain)
    await audit_commit_and_flash(
        session,
        request,
        action="domain_deleted",
        resource_type="domain",
        resource_id=str(domain_id),
        details={"name": domain_name},
        status_code=200,
        actor=current_user,
        flashes=(("info", f"Domain '{domain_name}' deleted."),),
    )
    await publish_resource_event("domain", "deleted", str(domain_id))
    return redirect_to("/domains")

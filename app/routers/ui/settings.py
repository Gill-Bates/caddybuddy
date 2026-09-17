#!/usr/bin/env python3
#
# app/routers/ui/settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.config.limiter import limiter, update_rate_limit_enabled
from app.config.settings import get_settings
from app.database.session import get_db_session
from app.dependencies.web import (
    initialize_user_session,
    push_flash,
    redirect_to,
    render_template,
)
from app.repositories.sites import site_repository
from app.repositories.users import user_repository
from app.services.auth import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    PASSWORD_POLICY_MESSAGE,
    WeakPasswordError,
    auth_service,
)
from app.services.caddy_onboarding import reset_onboarding_state
from app.services.caddyfile_manager import validate_and_deploy_full_caddyfile
from app.services.passkeys import (
    MAX_PASSKEYS_PER_USER,
    parse_transports,
    passkey_service,
)
from app.services.runtime_settings import (
    MAINTENANCE_PAGE_MAX_LENGTH,
    SSLLABS_RETENTION_DAY_VALUES,
    get_caddy_config,
    get_maintenance_page_html,
    get_rate_limit_enabled,
    get_ssllabs_email,
    get_ssllabs_history_retention_days,
    sanitize_maintenance_page_html,
    set_caddy_config,
    set_maintenance_page_html,
    set_rate_limit_enabled,
    set_ssllabs_email,
    set_ssllabs_history_retention_days,
)
from app.services.ssllabs import (
    check_email_registration_status,
    clear_registration_status_cache,
    register_email_with_ssllabs,
    ssllabs_service,
)
from app.utils.caddyfile import render_maintenance_page_html
from app.utils.otp import provisioning_qr_data_url
from app.utils.ssllabs import mask_email

from ._common import require_admin, require_onboarding_completed, validated_csrf_form

logger = logging.getLogger(__name__)
router = APIRouter()


def _expects_json_response(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    requested_with = request.headers.get("x-requested-with", "")
    return "application/json" in accept.lower() or requested_with.lower() == "xmlhttprequest"


def _settings_response(
    request: Request,
    *,
    success: bool,
    message: str,
    status_code: int = 200,
    background: BackgroundTask | None = None,
) -> Response:
    if _expects_json_response(request):
        response: Response = JSONResponse({"success": success, "message": message}, status_code=status_code)
    else:
        push_flash(request, "success" if success else "danger", message)
        response = redirect_to("/settings")

    response.background = background
    return response


@router.post("/settings/onboarding/restart", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def restart_onboarding_wizard(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    await validated_csrf_form(request)
    await reset_onboarding_state(session)
    await session.commit()
    if _expects_json_response(request):
        return JSONResponse({"success": True, "message": "Onboarding wizard restarted."})

    push_flash(request, "success", "Onboarding wizard restarted.")
    return redirect_to("/onboarding")


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")

    onboarding_redirect = await require_onboarding_completed(session)
    if onboarding_redirect is not None:
        return onboarding_redirect

    caddy_config = await get_caddy_config(session)
    rate_limit_enabled = await get_rate_limit_enabled(session)
    ssllabs_email = await get_ssllabs_email(session)
    masked_email = mask_email(ssllabs_email) if ssllabs_email else None
    ssllabs_retention_days = await get_ssllabs_history_retention_days(session)
    maintenance_page_html = await get_maintenance_page_html(session)
    passkeys = [
        {
            "id": passkey.id,
            "device_name": passkey.device_name,
            "transports": parse_transports(passkey.transports),
            "created_at": passkey.created_at,
            "last_used_at": passkey.last_used_at,
        }
        for passkey in await passkey_service.list_for_user(session, current_user)
    ]

    ssllabs_is_registered: bool | None = None
    if ssllabs_email:
        try:
            async with asyncio.timeout(5):
                ssllabs_is_registered = await check_email_registration_status(ssllabs_email)
        except TimeoutError:
            logger.warning("Timed out pre-fetching SSL Labs registration status for settings page.")
        except Exception:
            logger.warning(
                "Could not pre-fetch SSL Labs registration status for settings page.",
                exc_info=True,
            )

    context = {
        "caddy_api_url": caddy_config.admin_url,
        "caddyfile_path": caddy_config.caddyfile_path_str,
        "rate_limit_enabled": rate_limit_enabled,
        "maintenance_page_html": maintenance_page_html,
        "maintenance_page_max_length": MAINTENANCE_PAGE_MAX_LENGTH,
        "ssllabs_email": ssllabs_email,
        "ssllabs_masked_email": masked_email,
        "ssllabs_is_registered": ssllabs_is_registered,
        "ssllabs_retention_days": ssllabs_retention_days,
        "ssllabs_retention_values": list(SSLLABS_RETENTION_DAY_VALUES),
        "password_policy_min_length": PASSWORD_MIN_LENGTH,
        "password_policy_max_length": PASSWORD_MAX_LENGTH,
        "password_policy_message": PASSWORD_POLICY_MESSAGE,
        "otp_enabled": bool(getattr(current_user, "otp_enabled", False)),
        "otp_setup_pending": bool(getattr(current_user, "otp_secret", None)) and not bool(
            getattr(current_user, "otp_enabled", False)
        ),
        "passkeys": passkeys,
        "passkey_limit": MAX_PASSKEYS_PER_USER,
    }

    return render_template(request, "settings.html", current_user=current_user, context=context)


def _two_factor_setup_response(
    request: Request,
    current_user,
    *,
    error_message: str | None = None,
    status_code: int = 200,
) -> Response:
    secret = auth_service.pending_otp_secret(current_user)
    if secret is None:
        return redirect_to("/settings")
    provisioning_uri = auth_service.provisioning_uri(secret, current_user.username)
    response = render_template(
        request,
        "two_factor_setup.html",
        current_user=current_user,
        context={
            "otp_secret": secret,
            "otp_provisioning_uri": provisioning_uri,
            "otp_qr_data_url": provisioning_qr_data_url(provisioning_uri),
            "otp_error_message": error_message,
        },
        status_code=status_code,
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/settings/two-factor/enable", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def begin_two_factor_setup(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await validated_csrf_form(request)
    if not await auth_service.verify_password(str(form.get("current_password", "")), current_user.password_hash):
        return _settings_response(request, success=False, message="Current password is incorrect.", status_code=400)
    if await auth_service.begin_otp_enrollment(session, current_user) is None:
        return _settings_response(request, success=False, message="Two-factor authentication is already enabled.", status_code=400)
    await session.commit()
    initialize_user_session(request, current_user)
    return redirect_to("/settings/two-factor")


@router.get("/settings/two-factor", response_class=HTMLResponse)
async def two_factor_setup_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")
    return _two_factor_setup_response(request, current_user)


@router.post("/settings/two-factor/confirm", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def confirm_two_factor_setup(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await validated_csrf_form(request)
    recovery_codes = await auth_service.confirm_otp_enrollment(session, current_user, str(form.get("code", "")))
    if recovery_codes is None:
        return _two_factor_setup_response(
            request,
            current_user,
            error_message="Invalid authentication code. Try the current code from your authenticator app.",
            status_code=403,
        )
    await session.commit()
    initialize_user_session(request, current_user)
    response = render_template(
        request,
        "two_factor_recovery.html",
        current_user=current_user,
        context={"recovery_codes": recovery_codes},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/settings/two-factor/disable", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def disable_two_factor(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        return redirect_to("/login")
    form = await validated_csrf_form(request)
    if not await auth_service.verify_password(str(form.get("current_password", "")), current_user.password_hash):
        return _settings_response(request, success=False, message="Current password is incorrect.", status_code=400)
    if not await auth_service.disable_otp(session, current_user):
        return _settings_response(request, success=False, message="Could not disable two-factor authentication.", status_code=400)
    await session.commit()
    initialize_user_session(request, current_user)
    return _settings_response(request, success=True, message="Two-factor authentication disabled.")


@router.post("/settings/passkeys/{passkey_id}/delete", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def delete_passkey(
    request: Request,
    passkey_id: int,
    session: AsyncSession = Depends(get_db_session),
):
    """Remove one of the current user's passkeys.

    Deleting the last passkey is allowed: password sign-in (plus any configured
    second factor) always remains available, so this cannot lock the account out.
    """
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    if not await auth_service.verify_password(str(form.get("current_password", "")), current_user.password_hash):
        return _settings_response(request, success=False, message="Current password is incorrect.", status_code=400)
    if not await passkey_service.delete_for_user(session, current_user, passkey_id):
        await session.rollback()
        return _settings_response(request, success=False, message="Passkey not found.", status_code=404)

    await session.commit()
    return _settings_response(request, success=True, message="Passkey removed.")


@router.post("/settings/caddy", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def update_caddy_settings(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    caddy_api_url = form.get("caddy_api_url", "")
    caddyfile_path = form.get("caddyfile_path", "")
    rate_limit_enabled = form.get("rate_limit_enabled") == "on"

    try:
        await set_caddy_config(
            session,
            api_url=str(caddy_api_url),
            caddyfile_path=str(caddyfile_path),
        )
        await set_rate_limit_enabled(session, rate_limit_enabled)
    except ValueError as exc:
        return _settings_response(request, success=False, message=str(exc), status_code=400)

    await session.commit()
    # Deferred to a background task: slowapi's limiter decorator reads
    # `limiter.enabled` again after this handler returns (to decide whether to
    # inject rate-limit headers). Toggling the shared flag synchronously here
    # would flip that second read mid-flight and crash with UnboundLocalError
    # when this request itself was let through while disabled.
    return _settings_response(
        request,
        success=True,
        message="Settings updated.",
        background=BackgroundTask(update_rate_limit_enabled, rate_limit_enabled),
    )


@router.post("/settings/maintenance-page", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def update_maintenance_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    try:
        await set_maintenance_page_html(session, str(form.get("maintenance_page_html", "")))
    except ValueError as exc:
        return _settings_response(request, success=False, message=str(exc), status_code=400)

    # Stopped sites embed the page in the Caddy config, so they must be redeployed.
    sites = await site_repository.list_all(session, enabled_only=True)
    if any(site.maintenance_mode for site in sites):
        try:
            success, deploy_message = await validate_and_deploy_full_caddyfile(session)
        except Exception:
            await session.rollback()
            logger.exception("Deployment raised unexpectedly after maintenance page update")
            return _settings_response(
                request,
                success=False,
                message="Maintenance page was not saved: deployment failed unexpectedly.",
                status_code=500,
            )
        if not success:
            await session.rollback()
            return _settings_response(
                request,
                success=False,
                message=f"Maintenance page was not saved: {deploy_message}",
                status_code=502,
            )

    await session.commit()
    return _settings_response(request, success=True, message="Maintenance page saved.")


@router.post("/settings/maintenance-page/preview", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def preview_maintenance_page(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    try:
        sanitized = sanitize_maintenance_page_html(str(form.get("maintenance_page_html", "")))
    except ValueError as exc:
        return JSONResponse({"success": False, "message": str(exc)}, status_code=400)

    response = HTMLResponse(render_maintenance_page_html(sanitized))
    # This is inert, nh3-sanitized markup rendered for an admin-only preview iframe.
    # It needs its own policy: the page shell is styled entirely via inline `style`
    # attributes, and it must be embeddable by our own same-origin modal — both of
    # which the app-wide security headers (added only when a response doesn't already
    # set them, see SecurityHeadersMiddleware) would otherwise block.
    response.headers["content-security-policy"] = (
        "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"
    )
    response.headers["x-frame-options"] = "SAMEORIGIN"
    response.headers["cache-control"] = "no-store"
    return response


@router.post("/settings/ssllabs", response_class=HTMLResponse)
@limiter.limit("10/minute")
async def update_ssllabs_settings(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    ssllabs_email = str(form.get("ssllabs_email", ""))

    try:
        previous_email_raw = await get_ssllabs_email(session)
        previous_email = previous_email_raw.strip().lower() if previous_email_raw else None
        await set_ssllabs_email(session, ssllabs_email)
    except ValueError as exc:
        return _settings_response(request, success=False, message=str(exc), status_code=400)

    await session.commit()
    new_email = ssllabs_email.strip().lower() or None
    if previous_email and previous_email != new_email:
        clear_registration_status_cache(previous_email)
    if new_email:
        clear_registration_status_cache(new_email)
        try:
            async with asyncio.timeout(10):
                await ssllabs_service.startup()
        except TimeoutError:
            logger.warning("Timed out refreshing SSL Labs service after email update.")
            return _settings_response(
                request,
                success=False,
                message="SSL Labs email was saved, but the service refresh timed out.",
                status_code=504,
            )
        except Exception:
            logger.exception("Could not refresh SSL Labs service after email update.")
            return _settings_response(
                request,
                success=False,
                message="SSL Labs email was saved, but the service refresh failed.",
                status_code=500,
            )

        return _settings_response(request, success=True, message="SSL Labs email updated.")
    return _settings_response(request, success=True, message="SSL Labs email removed.")


@router.post("/settings/ssllabs-retention", response_class=HTMLResponse)
@limiter.limit("20/minute")
async def update_ssllabs_retention(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    raw_value = str(form.get("retention_days", "")).strip()
    try:
        retention_days = int(raw_value)
    except ValueError:
        return _settings_response(
            request, success=False, message="Retention value must be a whole number of days.", status_code=400
        )

    try:
        await set_ssllabs_history_retention_days(session, retention_days)
    except ValueError as exc:
        return _settings_response(request, success=False, message=str(exc), status_code=400)

    await session.commit()
    retention_label = "Unlimited" if retention_days == 0 else f"{retention_days} days"
    return _settings_response(request, success=True, message=f"SSL Labs history retention set to {retention_label}.")


@router.post("/settings/change-password", response_class=HTMLResponse)
@limiter.limit("5/minute")
async def change_password(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    form = await validated_csrf_form(request)
    current_password = str(form.get("current_password", ""))
    new_password = str(form.get("new_password", ""))
    confirm_password = str(form.get("confirm_password", ""))

    if not current_password or not new_password or not confirm_password:
        return _settings_response(request, success=False, message="All password fields are required.", status_code=400)

    if new_password != confirm_password:
        return _settings_response(request, success=False, message="New passwords do not match.", status_code=400)

    verified = await auth_service.verify_password(current_password, current_user.password_hash)
    if not verified:
        return _settings_response(request, success=False, message="Current password is incorrect.", status_code=400)

    try:
        new_hash = await auth_service.hash_password(new_password)
    except WeakPasswordError as e:
        return _settings_response(request, success=False, message=str(e), status_code=400)
    except ValueError as e:
        return _settings_response(request, success=False, message=str(e), status_code=400)

    await user_repository.update_password(session, current_user, new_hash)
    await session.commit()

    initialize_user_session(request, current_user)
    return _settings_response(request, success=True, message="Password changed successfully.")


@router.post("/settings/register-ssllabs", response_class=HTMLResponse)
@limiter.limit("3/hour")
async def register_ssllabs_email(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
):
    current_user = await require_admin(request, session)
    if current_user is None:
        if _expects_json_response(request):
            return JSONResponse({"success": False, "message": "Authentication required."}, status_code=401)
        return redirect_to("/login")

    settings = get_settings()
    email = await get_ssllabs_email(session)

    if not email:
        return _settings_response(request, success=False, message="No SSL Labs email configured.", status_code=400)

    try:
        async with asyncio.timeout(10):
            registered = await register_email_with_ssllabs(
                email=email,
                api_base_url=settings.ssllabs_api_base_url,
            )
    except TimeoutError:
        logger.warning("Timed out registering SSL Labs email.")
        return _settings_response(
            request,
            success=False,
            message="Registration timed out.",
            status_code=504,
        )
    except Exception:
        logger.exception("Unexpected SSL Labs registration failure.")
        return _settings_response(
            request,
            success=False,
            message="Registration failed due to an unexpected error.",
            status_code=500,
        )

    if registered:
        return _settings_response(request, success=True, message="Successfully registered with SSL Labs API.")
    return _settings_response(request, success=False, message="Registration failed.", status_code=400)

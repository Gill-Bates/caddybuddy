#!/usr/bin/env python3
#
# app/services/renewal.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import asyncio
import fcntl
import logging
from contextlib import contextmanager, suppress
from collections.abc import Callable, Awaitable
from pathlib import Path
from typing import Any

from app.config.settings import get_settings
from app.models.entities import Site
from app.services.caddy import CaddyAdminClient, CaddyServiceError, caddy_service
from app.services.certificates import (
    CertificateInfo,
    CertificateRenewalCapability,
    scan_certificate_storage,
    find_certificate_for_domain,
)
from app.services.dashboard import (
    get_certificate_info_for_domains,
    get_certificate_info_for_domains_remote,
    invalidate_certificate_cache,
)
from app.services.supervisor import get_caddy_supervisor
from app.services.runtime_settings import get_caddy_config
from app.utils.domains import split_domain_names
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

RenewalProgress = Callable[[str, dict[str, Any]], Awaitable[None]]
_SUCCESS_MESSAGES = {
    "restart_repair": "Caddy restarted and missing certificate artifacts were recreated successfully.",
    "acquisition_sync": "Certificate was acquired successfully.",
    "artifact_purge": "Certificate renewal succeeded.",
}


@contextmanager
def renewal_file_lock(lock_dir: Path, scope: str):
    """File-based lock using flock to prevent parallel certificate renewal runs across workers."""
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_scope = "".join(ch if ch.isalnum() or ch in ".-" else "_" for ch in scope)
    lock_path = lock_dir / f"cert-renewal-{safe_scope}.lock"

    with lock_path.open("w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"Certificate renewal is already running for {scope}.")
        try:
            yield
        finally:
            with suppress(Exception):
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


class CertificateRenewalService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.settings = get_settings()

    async def _with_control_timeout(self, awaitable: Awaitable[Any], operation: str) -> Any:
        try:
            async with asyncio.timeout(self.settings.caddy_control_timeout_seconds):
                return await awaitable
        except TimeoutError as exc:
            logger.warning("%s timed out", operation)
            raise CaddyServiceError(f"{operation} timed out.") from exc

    async def build_plan(
        self,
        site: Site,
        cert_info: CertificateInfo | None = None,
        *,
        fetch_if_missing: bool = True,
    ) -> CertificateRenewalCapability:
        """Analyze a site and determine the appropriate renewal capability/plan."""
        capability = await self._evaluate_renewal_capability(
            site, cert_info, fetch_if_missing=fetch_if_missing
        )
        if self.settings.caddy_control_mode == "disabled":
            # restart_repair needs a full Caddy restart, which requires a control mode.
            if capability.mode == "restart_repair":
                return CertificateRenewalCapability(
                    mode="unavailable",
                    reason="control_mode_disabled",
                    requires_confirmation=False,
                    scope_name=capability.scope_name,
                    scope_type=capability.scope_type,
                    wait_domains=capability.wait_domains,
                )
            # Forced purge would delete artifacts from disk; that is not allowed in
            # API-only deployments. Downgrade to a best-effort Admin API reload that
            # lets Caddy renew anything missing or within its renewal window.
            if capability.mode == "artifact_purge":
                return CertificateRenewalCapability(
                    mode="api_reload",
                    reason="control_mode_disabled",
                    requires_confirmation=False,
                    scope_name=capability.scope_name,
                    scope_type=capability.scope_type,
                    wait_domains=capability.wait_domains,
                )
        return capability

    async def _evaluate_renewal_capability(
        self,
        site: Site,
        cert_info: CertificateInfo | None = None,
        *,
        fetch_if_missing: bool = True,
    ) -> CertificateRenewalCapability:
        domains = split_domain_names(site.domain)
        if not domains:
            return CertificateRenewalCapability(
                mode="unavailable",
                reason="no_domains",
                requires_confirmation=False,
            )

        # Primary domain is the first one, but renewal waits on the full normalized domain set.
        domain = domains[0]
        
        if cert_info is None:
            if not fetch_if_missing:
                return CertificateRenewalCapability(
                    mode="unavailable",
                    reason="certificate_check_pending",
                    requires_confirmation=False,
                    scope_name=domain,
                    scope_type="domain",
                    wait_domains=domains,
                )
            # We need to fetch the current info for this domain
            domain_states = {d: (site.enabled, getattr(site, "updated_at", None)) for d in domains}
            infos = await get_certificate_info_for_domains(list(domains), managed_site_states=domain_states)
            cert_info = infos.get(domain)

        if not cert_info:
            return CertificateRenewalCapability(
                mode="unavailable",
                reason="check_failed",
                requires_confirmation=False,
                scope_name=domain,
                scope_type="domain",
                wait_domains=domains,
            )

        cert_status = getattr(cert_info, "status", "missing")
        if cert_status == "storage_unavailable":
            return CertificateRenewalCapability(
                mode="storage_unavailable",
                reason="storage_unreadable",
                requires_confirmation=False,
                scope_name=domain,
                scope_type="domain",
                wait_domains=domains,
            )

        is_wildcard = getattr(cert_info, "is_wildcard", False)
        covering_name = getattr(cert_info, "covering_name", None) or domain
        if is_wildcard:
            return CertificateRenewalCapability(
                mode="wildcard_scope_required",
                reason=covering_name,
                requires_confirmation=False,
                scope_name=covering_name,
                scope_type="wildcard",
                wait_domains=domains,
            )

        local_artifact_present = getattr(cert_info, "local_artifact_present", False)
        valid = getattr(cert_info, "valid", False)
        source = getattr(cert_info, "source", "none")
        local_artifact_complete = getattr(cert_info, "local_artifact_complete", False)
        artifact_scope = getattr(cert_info, "artifact_scope_name", None) or domain

        target_wait_domains = domains

        if not local_artifact_present:
            if valid and source == "remote":
                return CertificateRenewalCapability(
                    mode="restart_repair",
                    reason="local_artifact_missing",
                    requires_confirmation=self.settings.caddy_restart_confirmation_required,
                    scope_name=artifact_scope,
                    scope_type="domain",
                    wait_domains=target_wait_domains,
                )
            else:
                return CertificateRenewalCapability(
                    mode="acquisition_sync",
                    reason="local_artifact_missing",
                    requires_confirmation=False,
                    scope_name=artifact_scope,
                    scope_type="domain",
                    wait_domains=target_wait_domains,
                )
        elif not local_artifact_complete:
            # Present but incomplete (e.g. missing .key or .json)
            if valid:
                return CertificateRenewalCapability(
                    mode="restart_repair",
                    reason="local_artifact_incomplete",
                    requires_confirmation=self.settings.caddy_restart_confirmation_required,
                    scope_name=artifact_scope,
                    scope_type="domain",
                    wait_domains=target_wait_domains,
                )
            else:
                return CertificateRenewalCapability(
                    mode="acquisition_sync",
                    reason="local_artifact_incomplete",
                    requires_confirmation=False,
                    scope_name=artifact_scope,
                    scope_type="domain",
                    wait_domains=target_wait_domains,
                )

        return CertificateRenewalCapability(
            mode="artifact_purge",
            reason="standard_renewal",
            requires_confirmation=False,
            scope_name=getattr(cert_info, "artifact_scope_name", None) or domain,
            scope_type="domain",
            wait_domains=target_wait_domains,
        )

    async def execute(
        self,
        site: Site,
        plan: CertificateRenewalCapability,
        *,
        confirmed: bool = False,
        progress: RenewalProgress | None = None,
    ) -> tuple[bool, str]:
        """Execute the renewal plan."""
        trusted_plan = await self.build_plan(site, fetch_if_missing=True)
        if (
            plan.mode != trusted_plan.mode
            or plan.scope_name != trusted_plan.scope_name
            or plan.scope_type != trusted_plan.scope_type
            or plan.requires_confirmation != trusted_plan.requires_confirmation
            or tuple(plan.wait_domains) != tuple(trusted_plan.wait_domains)
        ):
            return False, "Renewal plan is stale or does not match the site."

        plan = trusted_plan
        target_domains = list(plan.wait_domains)
        if not target_domains:
            domains = split_domain_names(site.domain)
            target_domains = [domains[0].lower().strip()] if domains else []

        if not target_domains:
            return False, "Site has no renewal target domain."

        try:
            lock_scope = (plan.scope_name or target_domains[0]).lower().strip()
            with renewal_file_lock(self.settings.data_dir / "locks", lock_scope):
                if plan.requires_confirmation and not confirmed:
                    return False, "Confirmation required to proceed."

                if plan.mode == "restart_repair":
                    success, message = await self._execute_restart_repair(target_domains, progress)
                elif plan.mode == "acquisition_sync":
                    success, message = await self._execute_acquisition_sync(target_domains, progress)
                elif plan.mode == "artifact_purge":
                    success, message = await self._execute_artifact_purge(target_domains, plan, progress)
                elif plan.mode == "api_reload":
                    success, message = await self._execute_api_reload_renewal(target_domains, progress)
                elif plan.mode == "wildcard_scope_required":
                    return False, "Wildcard domains require manual or DNS-01 challenge configuration."
                elif plan.mode == "storage_unavailable":
                    return False, "Caddy certificate storage is inaccessible. Check file permissions."
                else:
                    return False, f"Renewal mode '{plan.mode}' is unavailable."

                if not success:
                    return False, message

                return True, message or _SUCCESS_MESSAGES.get(plan.mode, "Certificate renewal succeeded.")
        except CaddyServiceError as exc:
            return False, str(exc)
        except RuntimeError as e:
            return False, str(e)

    async def _publish(self, progress: RenewalProgress | None, action: str, payload: dict[str, Any]) -> None:
        if progress is not None:
            try:
                await progress(action, payload)
            except Exception:
                logger.warning("Renewal progress callback failed for action %s", action, exc_info=True)

    async def _execute_restart_repair(self, domains: list[str], progress: RenewalProgress | None) -> tuple[bool, str]:
        logger.info("Initiating restart_repair for domains: %s", domains)
        await self._publish(progress, "restarting_caddy", {})
        supervisor = await get_caddy_supervisor(self.session)
        restart_result = await self._with_control_timeout(supervisor.restart(), "Caddy restart")
        
        if not restart_result.success:
            logger.error("Caddy restart failed: %s", restart_result.error)
            return False, "Caddy restart failed during certificate repair."

        # Wait for API Health
        health_ok = await self._wait_for_caddy_health(timeout=30)
        if not health_ok:
            return False, "Caddy restarted but admin API did not become healthy."

        await self._publish(progress, "waiting_for_certificate", {})
        return await self._verify_renewal_success(domains)

    async def _execute_acquisition_sync(self, domains: list[str], progress: RenewalProgress | None) -> tuple[bool, str]:
        """Monitor certificate acquisition after the caller has already synced Caddy."""
        logger.info("Initiating acquisition_sync for domains: %s", domains)
        await self._publish(progress, "waiting_for_certificate", {})
        return await self._verify_renewal_success(domains)

    async def _execute_artifact_purge(
        self,
        domains: list[str],
        plan: CertificateRenewalCapability,
        progress: RenewalProgress | None,
    ) -> tuple[bool, str]:
        # artifact_purge deletes certificate artifacts from disk and then reloads Caddy
        # via its control mode. API-only deployments (no control mode) are routed to
        # api_reload during planning, so this path always has a control mode available.
        logger.info("Initiating artifact_purge for domains: %s", domains)
        primary_domain = domains[0]

        try:
            supervisor = await get_caddy_supervisor(self.session)
            status = await self._with_control_timeout(supervisor.status(), "Caddy supervisor status")
        except Exception:
            logger.exception("Unable to verify Caddy supervisor before certificate purge")
            return False, "Forced renewal needs a working Caddy control mode (systemd, Docker, or script). Configure and start it in Settings."
        if not status.success or status.status != "running":
            logger.warning("Caddy supervisor not ready for artifact purge: %s", status.error or status.status)
            return False, "Forced renewal needs a working Caddy control mode (systemd, Docker, or script). Configure and start it in Settings."

        try:
            deleted_count = await self._with_control_timeout(
                caddy_service.purge_certificate_artifacts(
                    plan.scope_name or primary_domain,
                    self.settings.caddy_certificates_path,
                    scope_type=plan.scope_type,
                    scope_name=plan.scope_name or primary_domain,
                ),
                "Certificate artifact purge",
            )
            if deleted_count == 0:
                return False, "Forced renewal could not proceed: no matching local certificate artifacts were removed."
            logger.info("Purged %d certificate artifacts for %s", deleted_count, primary_domain)
        except CaddyServiceError as exc:
            logger.warning("Certificate purge refused: %s", exc)
            return False, str(exc)
        except Exception:
            logger.exception("Error purging certificate artifacts")
            return False, "Error deleting certificate artifacts."

        reload_result = await self._with_control_timeout(supervisor.reload(), "Caddy reload")
        if not reload_result.success:
            logger.warning("Caddy reload failed after artifact purge; falling back to restart: %s", reload_result.error)
            await self._publish(progress, "restarting_caddy", {})
            restart_result = await self._with_control_timeout(supervisor.restart(), "Caddy restart")
            if not restart_result.success:
                logger.error("Caddy restart failed after artifact purge: %s", restart_result.error)
                return False, "Artifacts were purged, but Caddy reload and restart both failed."

            health_ok = await self._wait_for_caddy_health(timeout=30)
            if not health_ok:
                return False, "Caddy restarted but admin API did not become healthy."

            await self._publish(progress, "waiting_for_certificate", {})
            return await self._verify_renewal_success(domains)

        await self._publish(progress, "waiting_for_certificate", {})
        artifacts_ok = await self._wait_for_local_artifacts(domains, timeout=self.settings.cert_renewal_monitor_timeout_seconds)

        if not artifacts_ok:
            logger.warning("Artifacts not recreated after Caddy reload. Falling back to full Caddy restart.")
            await self._publish(progress, "restarting_caddy", {})
            restart_result = await supervisor.restart()
            if not restart_result.success:
                logger.error("Caddy restart fallback failed after artifact purge: %s", restart_result.error)
                return False, "Artifacts were purged, but Caddy restart fallback failed."

            health_ok = await self._wait_for_caddy_health(timeout=30)
            if not health_ok:
                return False, "Caddy restarted but admin API did not become healthy."

            await self._publish(progress, "waiting_for_certificate", {})

        return await self._verify_renewal_success(domains)

    async def _execute_api_reload_renewal(
        self,
        domains: list[str],
        progress: RenewalProgress | None,
    ) -> tuple[bool, str]:
        """Best-effort renewal for API-only deployments (no Caddy control mode).

        CaddyBuddy must not delete certificate artifacts from disk in this mode, and
        Caddy's Admin API has no force-renew endpoint. We trigger a config reload so
        Caddy renews certificates that are missing or within their renewal window; a
        still-valid certificate is intentionally left untouched.
        """
        caddy_config = await get_caddy_config(self.session)
        if not caddy_config.admin_url:
            return False, "Renewal requires a configured Caddy Admin API URL when no control mode is set."

        try:
            async with CaddyAdminClient(caddy_config.admin_url, self.settings.caddy_admin_timeout_seconds) as client:
                if not await client.health():
                    return False, "Caddy Admin API is not reachable. Cannot request a renewal reload."
                current_config = await client.get_config()
                await client.load_config_force(current_config, force_reload=True)
        except CaddyServiceError as exc:
            logger.warning("Admin API renewal reload failed: %s", exc)
            return False, f"Caddy Admin API reload failed: {exc}"
        except Exception:
            logger.exception("Admin API renewal reload failed")
            return False, "Caddy Admin API reload failed."

        logger.info("Requested Caddy Admin API reload for best-effort renewal: %s", domains)
        await self._publish(progress, "waiting_for_certificate", {})
        for domain in domains:
            try:
                await invalidate_certificate_cache(domain)
            except Exception:
                logger.warning("Failed to invalidate certificate cache for %s", domain, exc_info=True)

        return True, (
            "Requested a Caddy reload via the Admin API. Caddy renews the certificate only "
            "if it is missing or within its renewal window; a still-valid certificate is not "
            "force-renewed without a configured control mode."
        )

    async def _verify_renewal_success(self, domains: list[str]) -> tuple[bool, str]:
        artifacts_ok = await self._wait_for_local_artifacts(
            domains,
            timeout=self.settings.cert_renewal_monitor_timeout_seconds,
        )
        if not artifacts_ok:
            return False, "Certificate artifacts were not recreated."

        for domain in domains:
            try:
                await invalidate_certificate_cache(domain)
            except Exception:
                logger.warning("Failed to invalidate certificate cache for %s", domain, exc_info=True)

        try:
            remote_info = await get_certificate_info_for_domains_remote(domains)
        except Exception:
            logger.warning("Live TLS verification could not be completed for %s", domains, exc_info=True)
            return True, "Certificate artifacts were recreated. Live TLS verification could not be completed."

        blocking_errors = []
        remote_skipped = False
        for domain in domains:
            info = remote_info.get(domain)
            if info is None:
                continue
            if info.status == "remote_check_unavailable":
                remote_skipped = True
                continue
            if not info.valid:
                blocking_errors.append(f"{domain}: {info.error_message or info.status}")

        if blocking_errors:
            return False, "Live TLS verification failed: " + "; ".join(blocking_errors)

        if remote_skipped:
            return True, "Certificate artifacts were recreated. Live TLS verification was skipped for a private or non-public domain."

        return True, "Certificate artifacts were recreated and certificate state is valid."

    async def _wait_for_caddy_health(self, timeout: float) -> bool:
        start = asyncio.get_running_loop().time()
        config = await get_caddy_config(self.session)
        if not config.admin_url:
            return False
        poll_interval = max(0.5, self.settings.cert_renewal_poll_interval_seconds)

        while asyncio.get_running_loop().time() - start < timeout:
            try:
                async with CaddyAdminClient(config.admin_url, timeout_seconds=1.0) as client:
                    if await client.health():
                        return True
            except Exception as exc:
                logger.debug("Caddy health check failed during renewal monitoring: %s", exc)
            await asyncio.sleep(poll_interval)
        return False

    async def _wait_for_local_artifacts(self, domains: list[str], timeout: float) -> bool:
        start = asyncio.get_running_loop().time()
        saw_storage_error = False
        poll_interval = max(0.5, self.settings.cert_renewal_poll_interval_seconds)

        while asyncio.get_running_loop().time() - start < timeout:
            certs, storage_error = await asyncio.to_thread(scan_certificate_storage, self.settings.caddy_certificates_path)
            saw_storage_error = saw_storage_error or storage_error
            if not storage_error and all(
                (info := find_certificate_for_domain(certs, domain)) is not None
                and info.local_artifact_complete
                for domain in domains
            ):
                return True

            await asyncio.sleep(poll_interval)

        if saw_storage_error:
            logger.warning("Certificate storage remained unreadable while waiting for renewal artifacts.")
        return False

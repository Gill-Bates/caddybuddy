//
// app/static/js/app-status.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

(() => {
    "use strict";

    const App = window.CaddyBuddyApp || (window.CaddyBuddyApp = {});

    const resolveAppUrl = (rawUrl) => {
        if (typeof App.resolveSameOriginUrl === "function") {
            return App.resolveSameOriginUrl(rawUrl);
        }

        const url = new URL(rawUrl, window.location.origin);
        if (url.origin !== window.location.origin) {
            throw new Error("External URLs are not allowed.");
        }
        return url.toString();
    };

    const readJsonSafely = async (response) => {
        const contentType = response.headers.get("content-type") || "";
        if (!contentType.toLowerCase().includes("application/json")) {
            return {};
        }

        try {
            return await response.json();
        } catch {
            return {};
        }
    };

    const normalizeDomain = (value) => String(value || "").trim().toLowerCase();

    const fetchWithTimeout = async (rawUrl, options = {}, timeoutMs = 10000) => {
        if (typeof window.AbortController !== "function") {
            return fetch(rawUrl, options);
        }

        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
        const signals = [controller.signal];
        if (options.signal instanceof AbortSignal) {
            signals.push(options.signal);
        }
        try {
            return await fetch(rawUrl, {
                ...options,
                signal: AbortSignal.any(signals),
            });
        } finally {
            window.clearTimeout(timeoutId);
        }
    };

    App.initializeDashboardStatus = () => {
        const badge = document.getElementById("caddy-status-badge");
        const statusDot = document.getElementById("caddy-status-dot");
        const statusMeta = document.getElementById("caddy-status-meta");
        const versionWrapper = document.getElementById("caddy-version-wrapper");
        if (!(badge instanceof HTMLElement) || !(statusDot instanceof HTMLElement) || !(statusMeta instanceof HTMLElement)) {
            return;
        }
        if (typeof badge.dashboardStatusCleanup === "function") {
            badge.dashboardStatusCleanup();
        }
        if (!App.markInitialized(badge, "dashboardStatusInitialized")) {
            return;
        }

        const REFRESH_INTERVAL_MS = 10000;
        const MAX_SILENT_FAILURES = 3;
        const statusUrl = badge.dataset.statusUrl || "/api/v1/caddy/status";
        let failureCount = 0;
        let isUpdating = false;
        let intervalId = null;

        const updateVersion = (version) => {
            if (!(versionWrapper instanceof HTMLElement)) {
                return;
            }

            versionWrapper.replaceChildren();
            if (!version || version === "Unavailable" || version === "Unknown") {
                return;
            }

            versionWrapper.append("(");
            const badgeElement = document.createElement("span");
            badgeElement.className = "version-badge";
            badgeElement.id = "caddy-version";
            badgeElement.textContent = version;
            versionWrapper.append(badgeElement, ")");
        };

        const setUnavailableStatus = () => {
            statusDot.classList.remove("status-dot--online");
            statusDot.classList.add("status-dot--offline");
            statusMeta.textContent = "· Status unavailable";
        };

        const updateBadge = async () => {
            if (isUpdating) {
                return;
            }

            isUpdating = true;
            try {
                const response = await fetchWithTimeout(resolveAppUrl(statusUrl), {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                }, 10000);
                if (!response.ok) {
                    failureCount += 1;
                    if (failureCount >= MAX_SILENT_FAILURES) {
                        setUnavailableStatus();
                    }
                    return;
                }

                const data = await readJsonSafely(response);
                failureCount = 0;
                const isRunning = data.running === true;
                statusDot.classList.toggle("status-dot--online", isRunning);
                statusDot.classList.toggle("status-dot--offline", !isRunning);

                if (data.running && data.uptime && data.uptime !== "Unavailable") {
                    statusMeta.textContent = `· Uptime ${data.uptime}`;
                } else {
                    const statusText = typeof data.status === "string" && data.status.trim() !== ""
                        ? data.status.trim()
                        : "Unknown";
                    statusMeta.textContent = `· ${statusText}`;
                }
                updateVersion(data.version);
            } catch {
                failureCount += 1;
                if (failureCount >= MAX_SILENT_FAILURES) {
                    setUnavailableStatus();
                }
            } finally {
                isUpdating = false;
            }
        };

        const stopPolling = () => {
            if (intervalId !== null) {
                window.clearInterval(intervalId);
                intervalId = null;
            }
        };

        const handleVisibilityChange = () => {
            if (document.hidden) {
                stopPolling();
            } else if (intervalId === null) {
                updateBadge();
                intervalId = window.setInterval(updateBadge, REFRESH_INTERVAL_MS);
            }
        };

        badge.dashboardStatusCleanup = () => {
            stopPolling();
            document.removeEventListener("visibilitychange", handleVisibilityChange);
            window.removeEventListener("beforeunload", badge.dashboardStatusCleanup);
            delete badge.dataset.dashboardStatusInitialized;
            badge.dashboardStatusCleanup = null;
        };

        intervalId = window.setInterval(updateBadge, REFRESH_INTERVAL_MS);
        document.addEventListener("visibilitychange", handleVisibilityChange);
        window.addEventListener("beforeunload", badge.dashboardStatusCleanup, { once: true });

        updateBadge();
    };

    App.initializeDashboardMetrics = () => {
        const dashboard = document.querySelector("[data-dashboard-metrics-url]");
        const validCertificates = document.getElementById("dashboard-valid-certificates");
        const expiredCertificates = document.getElementById("dashboard-expired-certificates");
        const certificateWarning = document.getElementById("dashboard-certificate-warning");
        const certificateWarningText = document.getElementById("dashboard-certificate-warning-text");
        if (
            !(dashboard instanceof HTMLElement) ||
            !(validCertificates instanceof HTMLElement) ||
            !(expiredCertificates instanceof HTMLElement)
        ) {
            return;
        }
        if (!App.markInitialized(dashboard, "dashboardMetricsInitialized")) {
            return;
        }

        const metricsUrl = dashboard.dataset.dashboardMetricsUrl || "/api/v1/dashboard/metrics";

        const setMetricValue = (element, value) => {
            element.textContent = Number.isInteger(value) ? String(value) : "--";
        };

        const updateCertificateWarning = (count) => {
            if (!(certificateWarning instanceof HTMLElement) || !(certificateWarningText instanceof HTMLElement)) {
                return;
            }

            if (!Number.isInteger(count) || count <= 0) {
                certificateWarning.classList.add("d-none");
                certificateWarningText.textContent = "";
                return;
            }

            certificateWarning.classList.remove("d-none");
            certificateWarningText.textContent = `${count} certificate${count === 1 ? "" : "s"} expire within 7 days.`;
        };

        const refreshMetrics = async () => {
            try {
                const response = await fetchWithTimeout(resolveAppUrl(metricsUrl), {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                }, 10000);
                if (!response.ok) {
                    return;
                }

                const data = await readJsonSafely(response);
                setMetricValue(validCertificates, data.valid_certificate_count);
                setMetricValue(expiredCertificates, data.expired_certificate_count);
                updateCertificateWarning(data.expiring_soon_certificate_count);
            } catch {
                // Leave the shell values in place on transient failures.
            }
        };

        refreshMetrics();
    };

    App.initializeSslLabsStatus = () => {
        const statusEl = document.getElementById("ssllabs-status");
        const statusHintEl = document.getElementById("ssllabs-status-hint");
        const registerBtn = document.getElementById("ssllabs-register-btn");
        const refreshBtn = document.getElementById("ssllabs-refresh-status");

        if (!statusEl || !statusHintEl) {
            return;
        }
        if (!App.markInitialized(statusEl, "sslLabsStatusInitialized")) {
            return;
        }

        statusEl.setAttribute("aria-live", "polite");
        statusEl.setAttribute("aria-atomic", "true");
        statusHintEl.setAttribute("aria-live", "polite");
        statusHintEl.setAttribute("aria-atomic", "true");

        let sslLabsRequestId = 0;

        const isAbortError = (error) => error instanceof DOMException && error.name === "AbortError";

        const readRequiredCsrfToken = (missingMessage) => {
            const csrfToken = typeof App.readCsrfToken === "function" ? App.readCsrfToken() : "";
            if (!csrfToken) {
                statusHintEl.textContent = missingMessage;
                return null;
            }
            return csrfToken;
        };

        const setSslLabsControlsBusy = (busy) => {
            if (registerBtn instanceof HTMLButtonElement) {
                registerBtn.disabled = busy;
            }
            if (refreshBtn instanceof HTMLButtonElement) {
                refreshBtn.disabled = busy;
            }
        };

        const setStatusBadge = (element, className, text) => {
            element.textContent = "";
            const badge = document.createElement("span");
            badge.className = `badge cb-pill ${className}`;
            badge.textContent = text;
            element.appendChild(badge);
        };

        const updateStatusUI = (data) => {
            if (!data.masked_email) {
                setStatusBadge(statusEl, "bg-warning text-dark", "Not configured");
                statusHintEl.textContent = "Configure an SSL Labs email in the Settings page to enable scans.";
                if (registerBtn) {
                    registerBtn.classList.add("d-none");
                }
                return;
            }

            if (data.is_registered === true) {
                setStatusBadge(statusEl, "bg-success", "Registered");
                statusHintEl.textContent = "API access is active. You can run SSL Labs scans.";
                if (registerBtn) {
                    registerBtn.classList.add("d-none");
                }
            } else if (data.is_registered === false) {
                setStatusBadge(statusEl, "bg-danger", "Not registered");
                statusHintEl.textContent = "Email needs to be registered with SSL Labs API to run scans.";
                if (registerBtn) {
                    registerBtn.classList.remove("d-none");
                }
            } else {
                setStatusBadge(statusEl, "bg-secondary", "Unknown");
                statusHintEl.textContent = data.message || "Could not determine registration status.";
                if (registerBtn) {
                    registerBtn.classList.remove("d-none");
                }
            }
        };

        const showLoading = () => {
            statusEl.textContent = "";
            const spinner = document.createElement("span");
            spinner.className = "spinner-border spinner-border-sm text-secondary";
            spinner.setAttribute("role", "status");
            const srText = document.createElement("span");
            srText.className = "visually-hidden";
            srText.textContent = "Loading...";
            spinner.appendChild(srText);
            statusEl.appendChild(spinner);
            statusHintEl.textContent = "Checking registration status...";
        };

        const fetchStatus = async () => {
            const requestId = ++sslLabsRequestId;
            showLoading();
            try {
                const response = await fetchWithTimeout(resolveAppUrl("/api/v1/ssllabs/registration-status"), {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                }, 10000);
                if (!response.ok) {
                    throw new Error("Failed to fetch status");
                }
                const data = await readJsonSafely(response);
                if (requestId !== sslLabsRequestId) {
                    return;
                }
                updateStatusUI(data);
            } catch (error) {
                if (requestId !== sslLabsRequestId) {
                    return;
                }
                setStatusBadge(statusEl, "bg-warning text-dark", "Error");
                statusHintEl.textContent = isAbortError(error)
                    ? "Registration status check timed out."
                    : "Could not check registration status.";
                console.error("SSL Labs status check failed:", error);
            }
        };

        const registerEmail = async () => {
            if (!(registerBtn instanceof HTMLButtonElement)) {
                return;
            }
            const csrfToken = readRequiredCsrfToken("Security token is missing. Reload the page and try again.");
            if (!csrfToken) {
                registerBtn.textContent = "Register with SSL Labs";
                return;
            }

            const requestId = ++sslLabsRequestId;
            setSslLabsControlsBusy(true);
            registerBtn.textContent = "";
            const spinner = document.createElement("span");
            spinner.className = "spinner-border spinner-border-sm me-1";
            spinner.setAttribute("role", "status");
            registerBtn.appendChild(spinner);
            registerBtn.appendChild(document.createTextNode("Registering..."));

            try {
                const headers = {
                    Accept: "application/json",
                    "Content-Type": "application/json",
                    "X-CSRF-Token": csrfToken,
                };

                const response = await fetchWithTimeout(resolveAppUrl("/api/v1/ssllabs/register"), {
                    method: "POST",
                    credentials: "same-origin",
                    headers,
                }, 10000);
                const data = await readJsonSafely(response);

                if (requestId !== sslLabsRequestId) {
                    return;
                }

                if (response.ok && data.success) {
                    setStatusBadge(statusEl, "bg-success", "Registered");
                    statusHintEl.textContent = data.message || "Successfully registered with SSL Labs.";
                    registerBtn.classList.add("d-none");
                } else {
                    statusHintEl.textContent = data.detail || data.message || "Registration failed.";
                    registerBtn.textContent = "Register with SSL Labs";
                }
            } catch (error) {
                if (requestId !== sslLabsRequestId) {
                    return;
                }
                statusHintEl.textContent = isAbortError(error)
                    ? "Registration timed out. Please try again."
                    : "Registration request failed. Please try again.";
                registerBtn.textContent = "Register with SSL Labs";
                console.error("SSL Labs registration failed:", error);
            } finally {
                if (requestId === sslLabsRequestId) {
                    setSslLabsControlsBusy(false);
                }
            }
        };

        const refreshStatus = async () => {
            if (!(refreshBtn instanceof HTMLButtonElement)) {
                return;
            }
            const csrfToken = readRequiredCsrfToken("Security token is missing. Reload the page and try again.");
            if (!csrfToken) {
                return;
            }

            const requestId = ++sslLabsRequestId;
            setSslLabsControlsBusy(true);
            showLoading();

            try {
                const headers = { Accept: "application/json" };
                headers["X-CSRF-Token"] = csrfToken;

                const response = await fetchWithTimeout(resolveAppUrl("/api/v1/ssllabs/refresh-status"), {
                    method: "POST",
                    credentials: "same-origin",
                    headers,
                }, 10000);
                if (!response.ok) {
                    throw new Error("Failed to refresh status");
                }
                const data = await readJsonSafely(response);
                if (requestId !== sslLabsRequestId) {
                    return;
                }
                updateStatusUI(data);
            } catch (error) {
                if (requestId !== sslLabsRequestId) {
                    return;
                }
                setStatusBadge(statusEl, "bg-warning text-dark", "Error");
                statusHintEl.textContent = isAbortError(error)
                    ? "Registration status refresh timed out."
                    : "Could not refresh registration status.";
                console.error("SSL Labs status refresh failed:", error);
            } finally {
                if (requestId === sslLabsRequestId) {
                    setSslLabsControlsBusy(false);
                }
            }
        };

        if (registerBtn instanceof HTMLButtonElement && registerBtn.type === "button") {
            registerBtn.addEventListener("click", registerEmail);
        }
        if (refreshBtn) {
            refreshBtn.addEventListener("click", refreshStatus);
        }

        if (statusEl.dataset.ssllabsPreloaded === "true") {
            // Server already rendered the status badge; no need for an initial fetch.
            return;
        }

        fetchStatus();
    };

    const prepareRenewalConfirmation = (form) => {
        if (!(form instanceof HTMLFormElement)) {
            return;
        }
        if (form.dataset.renewalConfirmationInitialized === "true") {
            return;
        }

        const button = form.querySelector("[data-renew-certificate-button]");
        const confirmedInput = form.querySelector("[data-renewal-confirmed-input]");
        if (!(button instanceof HTMLButtonElement) || !(confirmedInput instanceof HTMLInputElement)) {
            return;
        }

        form.dataset.renewalConfirmationInitialized = "true";
        confirmedInput.value = "false";

        form.addEventListener("submit", (event) => {
            if (button.dataset.requiresConfirmation !== "true") {
                return;
            }

            if (confirmedInput.value === "true") {
                return;
            }

            event.preventDefault();

            const accepted = window.confirm(button.dataset.confirm || "Restart Caddy to repair certificate storage?");
            if (!accepted) {
                return;
            }

            confirmedInput.value = "true";
            form.requestSubmit(button);
        });
    };

    App.initializeSitesCertificates = () => {
        const page = document.querySelector("[data-sites-certificates-url]");
        if (!(page instanceof HTMLElement)) {
            return;
        }

        const renewalFormsBySiteId = new Map();

        const forms = page.querySelectorAll('form[action*="/renew-certificate"]');
        for (const form of forms) {
            prepareRenewalConfirmation(form);

            const action = form.getAttribute("action") || "";
            const match = /\/sites\/([^/]+)\/renew-certificate$/.exec(action);
            if (match) {
                renewalFormsBySiteId.set(decodeURIComponent(match[1]), form);
            }
        }

        const certificatesUrl = page.dataset.sitesCertificatesUrl;
        if (!certificatesUrl) {
            return;
        }

        const cells = Array.from(page.querySelectorAll("[data-site-certificate-domains], [data-site-certificate-domain]"))
            .filter((element) => element instanceof HTMLElement && (element.dataset.siteCertificateDomains || element.dataset.siteCertificateDomain))
            .map((element) => /** @type {HTMLElement} */(element));
        if (cells.length === 0) {
            return;
        }

        if (typeof page.sitesCertificatesCleanup === "function") {
            page.sitesCertificatesCleanup();
        }
        if (!App.markInitialized(page, "sitesCertificatesInitialized")) {
            return;
        }

        let refreshTimeoutId = null;
        const certificateSeverity = (cert) => {
            const status = typeof cert?.status === "string" ? cert.status : "missing";
            if (cert?.valid === true) {
                return 0;
            }

            switch (status) {
                case "pending":
                    return 1;
                case "missing":
                    return 2;
                case "expired":
                    return 3;
                case "error":
                case "remote_check_unavailable":
                    return 4;
                case "storage_unavailable":
                    return 5;
                default:
                    return typeof cert?.error_message === "string" && cert.error_message.trim() ? 4 : 2;
            }
        };

        const parseCertificateDomains = (cell) => {
            const rawDomains = cell.dataset.siteCertificateDomains;
            if (typeof rawDomains === "string" && rawDomains.trim()) {
                try {
                    const parsed = JSON.parse(rawDomains);
                    if (Array.isArray(parsed)) {
                        const normalized = parsed.map((value) => normalizeDomain(value)).filter(Boolean);
                        if (normalized.length > 0) {
                            return normalized;
                        }
                    }
                } catch {
                    // Fall through to the legacy single-domain attribute.
                }
            }

            const singleDomain = normalizeDomain(cell.dataset.siteCertificateDomain);
            return singleDomain ? [singleDomain] : [];
        };

        const pickWorstCertificate = (domains, certificates) => {
            let selectedCertificate = null;
            let selectedSeverity = -1;

            for (const domain of domains) {
                const cert = certificates[domain];
                if (!cert) {
                    continue;
                }
                const severity = certificateSeverity(cert);
                if (severity > selectedSeverity) {
                    selectedCertificate = cert;
                    selectedSeverity = severity;
                }
            }

            return selectedCertificate;
        };

        const cellsByDomain = new Map();
        for (const cell of cells) {
            for (const domain of parseCertificateDomains(cell)) {
                const existing = cellsByDomain.get(domain) || [];
                existing.push(cell);
                cellsByDomain.set(domain, existing);
            }
        }

        const escapeHtml = (value) => String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");

        const formatDate = (value) => {
            if (typeof value !== "string") {
                return "";
            }

            const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
            return match ? `${match[1]}-${match[2]}-${match[3]}` : "";
        };

        const renderCertificate = (cell, cert) => {
            const status = typeof cert?.status === "string" ? cert.status : "missing";
            const errorMessage = typeof cert?.error_message === "string" ? cert.error_message.trim() : "";
            const coveringName = typeof cert?.covering_name === "string" ? cert.covering_name.trim() : "";
            const source = typeof cert?.source === "string" ? cert.source : "none";
            const isWildcard = cert?.is_wildcard === true;

            if (status === "pending") {
                cell.innerHTML = `
                    <div class="site-cert__pending">Waiting for Caddy issuance…</div>
                `;
                return;
            }

            if (status === "storage_unavailable") {
                cell.innerHTML = `
                    <div class="site-cert__error site-cert__error--muted">
                        <div class="site-cert__error-title">Certificate storage unavailable</div>
                        <div class="site-cert__error-message">Local storage could not be read.</div>
                    </div>
                `;
                return;
            }

            if (status === "remote_check_unavailable") {
                const remoteCheckMessage = errorMessage || "Certificate checks are unavailable for this hostname.";
                cell.innerHTML = `
                    <div class="site-cert__error site-cert__error--muted">
                        <div class="site-cert__error-title">Remote check unavailable</div>
                        <div class="site-cert__error-message">${escapeHtml(remoteCheckMessage)}</div>
                    </div>
                `;
                return;
            }

            if (status === "error" || errorMessage !== "") {
                cell.innerHTML = `
                    <div class="site-cert__error">
                        <div class="site-cert__error-title">Certificate check failed</div>
                        <div class="site-cert__error-message">${escapeHtml(errorMessage || "Certificate check failed.")}</div>
                    </div>
                `;
                return;
            }

            if (!cert || cert.exists !== true) {
                cell.innerHTML = '<span class="site-cert__empty">No certificate</span>';
                return;
            }

            const isValid = cert.valid === true;
            const statusClass = isValid ? "status-dot--online" : "status-dot--offline";
            const summaryClass = isValid ? "site-cert__summary--valid" : "site-cert__summary--expired";
            const statusText = isValid ? "Valid" : "Expired";
            const issuedAt = formatDate(cert.issued_at);
            const daysMarkup = Number.isInteger(cert.days_remaining) && cert.days_remaining > 0
                ? `<span class="site-cert__days">${escapeHtml(`${cert.days_remaining}d remaining`)}</span>`
                : "";
            const issuedMarkup = issuedAt
                ? `<div class="site-cert__issued">Issued ${escapeHtml(issuedAt)}</div>`
                : "";
            const wildcardMarkup = isWildcard && coveringName
                ? `<div class="site-cert__issued">via ${escapeHtml(coveringName)}</div>`
                : "";
            const sourceMarkup = source === "remote"
                ? `<div class="site-cert__issued">Source remote</div>`
                : "";

            cell.innerHTML = `
                <div class="site-cert__summary ${summaryClass}">
                    <span class="status-dot ${statusClass}" aria-hidden="true"></span>
                    <span class="site-cert__status">${statusText}</span>
                    ${daysMarkup}
                </div>
                <div class="site-cert__meta">
                    ${issuedMarkup}
                    ${wildcardMarkup}
                    ${sourceMarkup}
                </div>
            `;
        };

        const renderCertificateRenewing = (cell) => {
            cell.innerHTML = `
                <div class="site-cert__summary site-cert__summary--renewing">
                    <span class="spinner-border spinner-border-sm text-primary me-2" role="status" aria-hidden="true"></span>
                    <span class="site-cert__status">Renewing...</span>
                </div>
            `;
        };

        const renderCertificateRestartingCaddy = (cell) => {
            cell.innerHTML = `
                <div class="site-cert__summary site-cert__summary--renewing">
                    <span class="spinner-border spinner-border-sm text-primary me-2" role="status" aria-hidden="true"></span>
                    <span class="site-cert__status">Restarting Caddy...</span>
                </div>
            `;
        };

        const renderCertificateWaitingForCertificate = (cell) => {
            cell.innerHTML = `
                <div class="site-cert__summary site-cert__summary--renewing">
                    <span class="spinner-border spinner-border-sm text-primary me-2" role="status" aria-hidden="true"></span>
                    <span class="site-cert__status">Waiting for cert...</span>
                </div>
            `;
        };

        const scheduleCertificateRefresh = () => {
            if (refreshTimeoutId !== null) {
                window.clearTimeout(refreshTimeoutId);
            }

            refreshTimeoutId = window.setTimeout(() => {
                refreshTimeoutId = null;
                refreshCertificates();
            }, 1000);
        };

        const refreshCertificates = async () => {
            try {
                const response = await fetchWithTimeout(resolveAppUrl(certificatesUrl), {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                }, 10000);
                if (!response.ok) {
                    return;
                }

                const payload = await readJsonSafely(response);
                const certificates = payload.certificates;
                const renewals = payload.renewals;

                if (!certificates || typeof certificates !== "object") {
                    return;
                }

                for (const cell of cells) {
                    const domains = parseCertificateDomains(cell);
                    if (domains.length === 0) {
                        continue;
                    }
                    const certificate = pickWorstCertificate(domains, certificates);
                    renderCertificate(cell, certificate);
                }
                
                if (renewals && typeof renewals === "object") {
                    for (const [siteId, renewal] of Object.entries(renewals)) {
                        if (!renewal || typeof renewal !== "object" || Array.isArray(renewal)) {
                            continue;
                        }
                        const form = renewalFormsBySiteId.get(String(siteId));
                        if (form) {
                            const btn = form.querySelector('button[type="submit"]');
                            const confirmedInput = form.querySelector('input[data-renewal-confirmed-input]');
                            if (btn && confirmedInput) {
                                btn.dataset.renewalMode = typeof renewal.mode === "string" ? renewal.mode : "";
                                btn.dataset.renewalReason = typeof renewal.reason === "string" ? renewal.reason : "";
                                btn.dataset.renewalScope = typeof renewal.scope_name === "string" ? renewal.scope_name : "";
                                btn.dataset.renewalScopeType = typeof renewal.scope_type === "string" ? renewal.scope_type : "domain";
                                btn.dataset.renewalWaitDomains = JSON.stringify(Array.isArray(renewal.wait_domains) ? renewal.wait_domains : []);
                                btn.dataset.requiresConfirmation = renewal.requires_confirmation ? "true" : "false";
                                
                                const siteName = btn.getAttribute("aria-label")?.replace("Renew certificate for ", "") || "this site";
                                let confirmMsg = `Trigger certificate renewal for ${siteName}? Caddy will request a new certificate if the current one is within the renewal window.`;
                                if (renewal.mode === "restart_repair") {
                                    confirmMsg = `Trigger certificate renewal for ${siteName}? This requires a full Caddy restart to repair local artifacts.`;
                                }
                                btn.dataset.confirm = confirmMsg;
                                
                                const disabled = ["unavailable", "storage_unavailable", "wildcard_scope_required"].includes(renewal.mode);
                                btn.disabled = disabled;
                                if (disabled) {
                                    btn.setAttribute("aria-disabled", "true");
                                } else {
                                    btn.removeAttribute("aria-disabled");
                                }
                            }
                        }
                    }
                }
            } catch {
                // Keep cached or placeholder certificate state on transient failures.
            }
        };

        const handleCertificateEvent = (event) => {
            try {
                const payload = JSON.parse(event.data);
                if (payload.type !== "certificate") {
                    return;
                }

                const domain = normalizeDomain(payload.id);
                if (!domain) {
                    return;
                }

                const domainCells = cellsByDomain.get(domain);
                if (!Array.isArray(domainCells) || domainCells.length === 0) {
                    return;
                }

                if (payload.action === "renewing") {
                    for (const cell of domainCells) {
                        renderCertificateRenewing(cell);
                    }
                } else if (payload.action === "restarting_caddy") {
                    for (const cell of domainCells) {
                        renderCertificateRestartingCaddy(cell);
                    }
                } else if (payload.action === "waiting_for_certificate") {
                    for (const cell of domainCells) {
                        renderCertificateWaitingForCertificate(cell);
                    }
                } else if (payload.action === "renewed" || payload.action === "renewal_failed") {
                    scheduleCertificateRefresh();
                }
            } catch {
                // Ignore malformed event payloads.
            }
        };

        if (typeof App.addSseEventListener === "function") {
            const cleanupSitesCertificates = () => {
                if (refreshTimeoutId !== null) {
                    window.clearTimeout(refreshTimeoutId);
                    refreshTimeoutId = null;
                }
                if (typeof App.removeSseEventListener === "function") {
                    App.removeSseEventListener(handleCertificateEvent);
                }
                window.removeEventListener("beforeunload", cleanupSitesCertificates);
                delete page.dataset.sitesCertificatesInitialized;
                if (page.sitesCertificatesCleanup === cleanupSitesCertificates) {
                    page.sitesCertificatesCleanup = null;
                }
            };

            App.addSseEventListener(handleCertificateEvent);
            page.sitesCertificatesCleanup = cleanupSitesCertificates;
            window.addEventListener("beforeunload", cleanupSitesCertificates, { once: true });
        }

        refreshCertificates();
    };

    if (!document.body.classList.contains('app-body--public')) {
        App.initializeLiveUpdates?.();
    }
    App.initializeMobileMenu?.();
    App.initializeLoadingSubmitForms?.();
    App.initializeAutoDismissToasts?.();
    App.initializeSettingsAutoSave?.();
    App.initializeAutoSubmitForms?.();
    App.initializeSiteDomainInputs?.();
    App.initializeSiteConfigForms?.();
    App.initializeSiteFormModal?.();
    App.initializeSitesSearch?.();
    App.initializeCaddyfileForms?.();
    App.initializeValidateButtons?.();
    App.initializeDashboardStatus?.();
    App.initializeDashboardMetrics?.();
    App.initializeSettingsPasswordValidation?.();
    App.initializeResponsiveCodeTextareas?.();
    App.initializeSslLabsStatus?.();
    App.initializeSitesCertificates?.();
})();

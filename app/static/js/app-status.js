//
// app/static/js/app-status.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

(() => {
    "use strict";

    const App = window.CaddyBuddyApp || (window.CaddyBuddyApp = {});

    const markInitialized = (element, datasetKey) => {
        if (!(element instanceof HTMLElement)) {
            return false;
        }
        if (element.dataset[datasetKey] === "true") {
            return false;
        }
        element.dataset[datasetKey] = "true";
        return true;
    };

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
        if (!markInitialized(badge, "dashboardStatusInitialized")) {
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
                const response = await fetch(resolveAppUrl(statusUrl), {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                });
                if (!response.ok) {
                    failureCount += 1;
                    if (failureCount >= MAX_SILENT_FAILURES) {
                        setUnavailableStatus();
                    }
                    return;
                }

                const data = await readJsonSafely(response);
                failureCount = 0;
                statusDot.classList.toggle("status-dot--online", data.running);
                statusDot.classList.toggle("status-dot--offline", !data.running);

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
        if (!markInitialized(dashboard, "dashboardMetricsInitialized")) {
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
                const response = await fetch(resolveAppUrl(metricsUrl), {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                });
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
        if (!markInitialized(statusEl, "sslLabsStatusInitialized")) {
            return;
        }

        const setSslLabsControlsBusy = (busy) => {
            if (registerBtn instanceof HTMLButtonElement && !registerBtn.classList.contains("d-none")) {
                registerBtn.disabled = busy;
            }
            if (refreshBtn instanceof HTMLButtonElement) {
                refreshBtn.disabled = busy;
            }
        };

        const setStatusBadge = (element, className, text) => {
            element.textContent = "";
            const badge = document.createElement("span");
            badge.className = `badge ${className}`;
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
            showLoading();
            try {
                const response = await fetch(resolveAppUrl("/api/v1/ssllabs/registration-status"), {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                });
                if (!response.ok) {
                    throw new Error("Failed to fetch status");
                }
                const data = await readJsonSafely(response);
                updateStatusUI(data);
            } catch (error) {
                setStatusBadge(statusEl, "bg-warning text-dark", "Error");
                statusHintEl.textContent = "Could not check registration status.";
                console.error("SSL Labs status check failed:", error);
            }
        };

        const registerEmail = async () => {
            if (!(registerBtn instanceof HTMLButtonElement)) {
                return;
            }
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
                };
                const csrfToken = App.readCsrfToken();
                if (csrfToken) {
                    headers["X-CSRF-Token"] = csrfToken;
                }

                const response = await fetch(resolveAppUrl("/api/v1/ssllabs/register"), {
                    method: "POST",
                    credentials: "same-origin",
                    headers,
                });
                const data = await readJsonSafely(response);

                if (response.ok && data.success) {
                    setStatusBadge(statusEl, "bg-success", "Registered");
                    statusHintEl.textContent = data.message || "Successfully registered with SSL Labs.";
                    registerBtn.classList.add("d-none");
                } else {
                    statusHintEl.textContent = data.detail || data.message || "Registration failed.";
                    registerBtn.textContent = "Register with SSL Labs";
                }
            } catch (error) {
                statusHintEl.textContent = "Registration request failed. Please try again.";
                registerBtn.textContent = "Register with SSL Labs";
                console.error("SSL Labs registration failed:", error);
            } finally {
                setSslLabsControlsBusy(false);
            }
        };

        const refreshStatus = async () => {
            if (!(refreshBtn instanceof HTMLButtonElement)) {
                return;
            }
            setSslLabsControlsBusy(true);
            showLoading();

            try {
                const headers = { Accept: "application/json" };
                const csrfToken = App.readCsrfToken();
                if (csrfToken) {
                    headers["X-CSRF-Token"] = csrfToken;
                }

                const response = await fetch(resolveAppUrl("/api/v1/ssllabs/refresh-status"), {
                    method: "POST",
                    credentials: "same-origin",
                    headers,
                });
                if (!response.ok) {
                    throw new Error("Failed to refresh status");
                }
                const data = await readJsonSafely(response);
                updateStatusUI(data);
            } catch (error) {
                setStatusBadge(statusEl, "bg-warning text-dark", "Error");
                statusHintEl.textContent = "Could not refresh registration status.";
                console.error("SSL Labs status refresh failed:", error);
            } finally {
                setSslLabsControlsBusy(false);
            }
        };

        if (registerBtn instanceof HTMLButtonElement && registerBtn.type === "button") {
            registerBtn.addEventListener("click", registerEmail);
        }
        if (refreshBtn) {
            refreshBtn.addEventListener("click", refreshStatus);
        }

        fetchStatus();
    };

    App.initializeSitesCertificates = () => {
        const page = document.querySelector("[data-sites-certificates-url]");
        if (!(page instanceof HTMLElement)) {
            return;
        }
        if (typeof page.sitesCertificatesCleanup === "function") {
            page.sitesCertificatesCleanup();
        }
        if (!markInitialized(page, "sitesCertificatesInitialized")) {
            return;
        }

        const certificatesUrl = page.dataset.sitesCertificatesUrl;
        if (!certificatesUrl) {
            return;
        }

        const cells = Array.from(page.querySelectorAll("[data-site-certificate-domain]"))
            .filter((element) => element instanceof HTMLElement && element.dataset.siteCertificateDomain)
            .map((element) => /** @type {HTMLElement} */(element));
        if (cells.length === 0) {
            return;
        }

        const cellsByDomain = new Map();
        for (const cell of cells) {
            const domain = normalizeDomain(cell.dataset.siteCertificateDomain);
            if (!domain) {
                continue;
            }
            const existing = cellsByDomain.get(domain) || [];
            existing.push(cell);
            cellsByDomain.set(domain, existing);
        }

        const escapeHtml = (value) => String(value)
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#39;");

        const formatDate = (value) => {
            if (typeof value !== "string" || value.length === 0) {
                return "";
            }
            const parsed = new Date(value);
            if (Number.isNaN(parsed.getTime())) {
                return "";
            }
            return parsed.toISOString().slice(0, 10);
        };

        const renderCertificate = (cell, domain, cert) => {
            if (cert && typeof cert.error_message === "string" && cert.error_message.trim() !== "") {
                cell.innerHTML = `
                    <div class="site-cert__error">
                        <div class="site-cert__error-title">Certificate check failed</div>
                        <div class="site-cert__error-message">${escapeHtml(cert.error_message)}</div>
                    </div>
                `;
                return;
            }

            if (!cert || cert.exists !== true) {
                cell.innerHTML = '<span class="site-cert__empty">No certificate</span>';
                return;
            }

            const statusClass = cert.valid ? "status-dot--online" : "status-dot--offline";
            const summaryClass = cert.valid ? "site-cert__summary--valid" : "site-cert__summary--expired";
            const statusText = cert.valid ? "Valid" : "Expired";
            const issuedAt = formatDate(cert.issued_at);
            const daysMarkup = Number.isInteger(cert.days_remaining) && cert.days_remaining > 0
                ? `<span class="site-cert__days">${escapeHtml(`${cert.days_remaining}d remaining`)}</span>`
                : "";
            const issuedMarkup = issuedAt
                ? `<div class="site-cert__issued">Issued ${escapeHtml(issuedAt)}</div>`
                : "";

            cell.innerHTML = `
                <div class="site-cert__summary ${summaryClass}">
                    <span class="status-dot ${statusClass}" aria-hidden="true"></span>
                    <span class="site-cert__status">${statusText}</span>
                    ${daysMarkup}
                </div>
                <div class="site-cert__meta">
                    ${issuedMarkup}
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

        const refreshCertificates = async () => {
            try {
                const response = await fetch(resolveAppUrl(certificatesUrl), {
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                });
                if (!response.ok) {
                    return;
                }

                const payload = await readJsonSafely(response);
                const certificates = payload.certificates;
                if (!certificates || typeof certificates !== "object") {
                    return;
                }

                for (const [domain, cert] of Object.entries(certificates)) {
                    const normalizedDomain = normalizeDomain(domain);
                    const domainCells = cellsByDomain.get(normalizedDomain);
                    if (!Array.isArray(domainCells)) {
                        continue;
                    }
                    for (const cell of domainCells) {
                        renderCertificate(cell, normalizedDomain, cert);
                    }
                }
            } catch {
                // Keep cached or placeholder certificate state on transient failures.
            }
        };

        // Handle certificate SSE events for real-time updates
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
                } else if (payload.action === "renewed" || payload.action === "renewal_failed") {
                    // Refresh certificates after a short delay to get updated info
                    window.setTimeout(refreshCertificates, 1000);
                }
            } catch {
                // Ignore malformed event payloads.
            }
        };

        // Set up SSE listener for certificate events
        if (typeof App.addSseEventListener === "function") {
            const cleanupSitesCertificates = () => {
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
    App.initializeCaddyfileForms?.();
    App.initializeValidateButtons?.();
    App.initializeDashboardStatus?.();
    App.initializeDashboardMetrics?.();
    App.initializeSettingsPasswordValidation?.();
    App.initializeResponsiveCodeTextareas?.();
    App.initializeSslLabsStatus?.();
    App.initializeSitesCertificates?.();
})();
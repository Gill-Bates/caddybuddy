//
// app/static/js/about.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

(function () {
    "use strict";

    const CHECK_UPDATES_URL = "/about/check-updates";

    function safeReleaseUrl(raw) {
        try {
            const url = new URL(String(raw || ""), window.location.origin);
            return url.protocol === "https:" ? url.href : null;
        } catch {
            return null;
        }
    }

    function parseVersionParts(version) {
        if (!version || typeof version !== "string") return [0];
        const match = version.trim().replace(/^v/i, "").match(/^(\d+(?:\.\d+)*)/);
        if (!match) return [0];
        return match[1].split(".").map((part) => Number.parseInt(part, 10) || 0);
    }

    function compareVersions(a, b) {
        const pa = parseVersionParts(a);
        const pb = parseVersionParts(b);
        const len = Math.max(pa.length, pb.length);
        for (let i = 0; i < len; i += 1) {
            const va = pa[i] || 0;
            const vb = pb[i] || 0;
            if (va > vb) return 1;
            if (va < vb) return -1;
        }
        return 0;
    }

    function setUpdateStatusBadge(container, type, leadText, tailText) {
        if (!container) return;
        container.replaceChildren();
        const alert = document.createElement("div");
        alert.className = `alert alert-${type} py-1 mb-0 small`;
        if (leadText) {
            const strong = document.createElement("strong");
            strong.textContent = leadText;
            alert.appendChild(strong);
            if (tailText) alert.appendChild(document.createTextNode(` ${tailText}`));
        } else if (tailText) {
            alert.appendChild(document.createTextNode(tailText));
        }
        container.appendChild(alert);
    }

    async function checkForUpdates(force, isManualCheck) {
        const loadingEl = document.getElementById("update-check-loading");
        const resultEl = document.getElementById("update-check-result");
        const statusBadge = document.getElementById("update-status-badge");
        const currentVersionEl = document.getElementById("update-current-version");
        const latestVersionEl = document.getElementById("update-latest-version");
        const publishedRow = document.getElementById("update-published-row");
        const publishedAtEl = document.getElementById("update-published-at");
        const errorEl = document.getElementById("update-error");
        const errorTextEl = document.getElementById("update-error-text");
        const availableSectionEl = document.getElementById("update-available-section");
        const releaseLinkEl = document.getElementById("update-release-link");
        const checkBtn = document.getElementById("btn-check-updates");

        if (!loadingEl || !resultEl) return;

        loadingEl.classList.remove("d-none");
        resultEl.classList.add("d-none");
        if (checkBtn) checkBtn.disabled = true;

        try {
            const url = force ? `${CHECK_UPDATES_URL}?force=true` : CHECK_UPDATES_URL;
            const response = await fetch(url, {
                method: "GET",
                headers: { Accept: "application/json" },
                credentials: "same-origin",
            });
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            const res = await response.json();

            loadingEl.classList.add("d-none");
            resultEl.classList.remove("d-none");

            currentVersionEl.textContent = res.current_version || "–";
            latestVersionEl.textContent = res.latest_version || "–";

            if (res.published_at) {
                publishedRow.classList.remove("d-none");
                publishedAtEl.textContent = new Date(res.published_at).toLocaleDateString(undefined, {
                    year: "numeric",
                    month: "short",
                    day: "numeric",
                });
            } else {
                publishedRow.classList.add("d-none");
            }

            const currentIsHigher =
                res.latest_version && compareVersions(res.current_version || "", res.latest_version) > 0;

            if (currentIsHigher) {
                setUpdateStatusBadge(
                    statusBadge,
                    "warning",
                    null,
                    "You are running a pre-release. This is not recommended for production.",
                );
                availableSectionEl.classList.add("d-none");
            } else if (res.update_available) {
                setUpdateStatusBadge(statusBadge, "success", "Update available!", `Version ${res.latest_version || "–"} is ready.`);
                availableSectionEl.classList.remove("d-none");
                const releaseUrl = safeReleaseUrl(res.release_url);
                if (releaseUrl) {
                    releaseLinkEl.href = releaseUrl;
                } else {
                    releaseLinkEl.removeAttribute("href");
                }
            } else {
                setUpdateStatusBadge(statusBadge, "secondary", null, "You're running the latest version.");
                availableSectionEl.classList.add("d-none");
            }

            if (res.error) {
                const isNetworkError =
                    res.error.includes("Network error") ||
                    res.error.includes("Connection timeout") ||
                    res.error.includes("No address associated");
                if (isManualCheck || !isNetworkError) {
                    errorEl.classList.remove("d-none");
                    errorTextEl.textContent = res.error;
                } else {
                    errorEl.classList.add("d-none");
                }
            } else {
                errorEl.classList.add("d-none");
            }
        } catch (error) {
            loadingEl.classList.add("d-none");
            resultEl.classList.remove("d-none");
            if (statusBadge) statusBadge.replaceChildren();
            if (isManualCheck) {
                errorEl.classList.remove("d-none");
                errorTextEl.textContent = `Failed to check for updates: ${error.message || error}`;
            } else {
                errorEl.classList.add("d-none");
            }
        } finally {
            if (checkBtn) checkBtn.disabled = false;
        }
    }

    function initChangelogDetailsScroll() {
        document.querySelectorAll(".about-changelog-content details").forEach((details) => {
            details.addEventListener("toggle", () => {
                if (details.open) details.scrollTop = 0;
            });
        });
    }

    const checkBtn = document.getElementById("btn-check-updates");
    const isAdmin = checkBtn?.dataset?.isAdmin === "true";
    checkBtn?.addEventListener("click", () => checkForUpdates(isAdmin, true));
    initChangelogDetailsScroll();
    checkForUpdates(false, false);
})();

//
// app/static/js/settings-retention.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

// SSL Labs rank-history retention slider. Maps a discrete slider index to an allowed
// day value and persists it to the server on release.

(() => {
    "use strict";

    const App = window.CaddyBuddyApp || (window.CaddyBuddyApp = {});

    const formatLabel = (days) => {
        const value = Number(days);
        if (value === 365) {
            return "1 year";
        }
        return `${value} days`;
    };

    const parseValues = (raw) => {
        try {
            const parsed = JSON.parse(raw || "[]");
            if (Array.isArray(parsed)) {
                return parsed.map(Number).filter((value) => Number.isFinite(value));
            }
        } catch (error) {
            console.error("Invalid retention values:", error);
        }
        return [];
    };

    const initialize = () => {
        const root = document.querySelector("[data-ssllabs-retention]");
        if (!root) {
            return;
        }
        if (typeof App.markInitialized === "function"
            && !App.markInitialized(root, "ssllabsRetentionInitialized")) {
            return;
        }

        const slider = root.querySelector("[data-retention-slider]");
        const badge = root.querySelector("[data-retention-badge]");
        const values = parseValues(root.getAttribute("data-retention-values"));
        const endpoint = root.getAttribute("data-retention-url");
        if (!(slider instanceof HTMLInputElement) || values.length === 0 || !endpoint) {
            return;
        }

        const current = Number(root.getAttribute("data-retention-current"));
        const startIndex = Math.max(0, values.indexOf(current));
        slider.value = String(startIndex >= 0 ? startIndex : values.length - 1);

        let lastSavedIndex = Number(slider.value);

        const indexToDays = (index) => values[Math.max(0, Math.min(values.length - 1, index))];

        const updateBadge = (index) => {
            if (badge) {
                badge.textContent = formatLabel(indexToDays(index));
            }
        };

        const flashBadge = (variant) => {
            if (!badge) {
                return;
            }
            const target = variant === "success" ? "text-bg-success" : "text-bg-danger";
            badge.classList.remove("text-bg-secondary");
            badge.classList.add(target);
            window.setTimeout(() => {
                badge.classList.remove(target);
                badge.classList.add("text-bg-secondary");
            }, 1200);
        };

        const resolveUrl = (rawUrl) => (
            typeof App.resolveSameOriginUrl === "function"
                ? App.resolveSameOriginUrl(rawUrl)
                : new URL(rawUrl, window.location.origin).toString()
        );

        const save = async (index) => {
            const days = indexToDays(index);
            const body = new URLSearchParams();
            body.set("retention_days", String(days));
            const csrf = typeof App.readCsrfToken === "function" ? App.readCsrfToken() : "";
            if (csrf) {
                body.set("csrf_token", csrf);
            }
            slider.disabled = true;
            try {
                const response = await fetch(resolveUrl(endpoint), {
                    method: "POST",
                    credentials: "same-origin",
                    headers: {
                        Accept: "application/json",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                    body: body.toString(),
                });
                const payload = await response.json().catch(() => ({}));
                const responseMessage = typeof payload.message === "string" && payload.message
                    ? payload.message
                    : "";
                if (!response.ok || payload.success === false) {
                    throw new Error(responseMessage || `Request failed: ${response.status}`);
                }
                const message = responseMessage || `SSL Labs history retention set to ${formatLabel(days)}.`;
                lastSavedIndex = index;
                flashBadge("success");
                App.pushInlineFlash?.("success", message);
            } catch (error) {
                const message = error instanceof Error && error.message
                    ? error.message
                    : "Failed to save SSL Labs history retention.";
                console.error("Failed to save retention:", error);
                slider.value = String(lastSavedIndex);
                updateBadge(lastSavedIndex);
                flashBadge("danger");
                App.pushInlineFlash?.("danger", message);
            } finally {
                slider.disabled = false;
            }
        };

        updateBadge(startIndex);

        slider.addEventListener("input", () => updateBadge(Number(slider.value)));
        slider.addEventListener("change", () => {
            const index = Number(slider.value);
            if (index !== lastSavedIndex) {
                save(index).catch((error) => console.error(error));
            }
        });
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
        initialize();
    }
})();

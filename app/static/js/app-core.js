//
// app/static/js/app-core.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

(() => {
    "use strict";

    const App = window.CaddyBuddyApp || (window.CaddyBuddyApp = {});

    App.allowedConfirmButtonClasses = new Set([
        "btn-primary",
        "btn-secondary",
        "btn-danger",
        "btn-warning",
    ]);

    App.allowedFlashCategories = new Set([
        "success",
        "danger",
        "warning",
        "info",
        "primary",
        "secondary",
    ]);

    App.readCsrfToken = (form = null) => {
        if (form instanceof HTMLFormElement) {
            const tokenInput = form.elements.namedItem("csrf_token");
            if (tokenInput instanceof HTMLInputElement && typeof tokenInput.value === "string") {
                return tokenInput.value;
            }
        }

        const metaToken = document.querySelector("meta[name='csrf-token']");
        if (metaToken instanceof HTMLMetaElement && metaToken.content) {
            return metaToken.content;
        }

        return document.body?.dataset.csrfToken || "";
    };

    App.resolveSameOriginUrl = (rawUrl) => {
        const url = new URL(rawUrl, window.location.origin);
        if (url.origin !== window.location.origin) {
            throw new Error("External URLs are not allowed.");
        }
        return url.toString();
    };

    App.serializeComparableFormState = (form) => {
        if (!(form instanceof HTMLFormElement)) {
            return "";
        }

        const formData = new FormData(form);
        const entries = [];
        for (const [key, value] of formData.entries()) {
            if (key === "csrf_token" || value instanceof File) {
                continue;
            }
            entries.push([key, String(value)]);
        }

        entries.sort(([leftKey, leftValue], [rightKey, rightValue]) => {
            if (leftKey === rightKey) {
                return leftValue.localeCompare(rightValue);
            }
            return leftKey.localeCompare(rightKey);
        });
        return JSON.stringify(entries);
    };

    App.hasUnsavedManagedFormChanges = () => {
        for (const form of document.querySelectorAll("form[data-site-config-form]")) {
            if (!(form instanceof HTMLFormElement)) {
                continue;
            }

            const initialState = form.dataset.initialSerializedState;
            if (typeof initialState === "string" && initialState !== "" && App.serializeComparableFormState(form) !== initialState) {
                return true;
            }
        }

        for (const form of document.querySelectorAll("form[data-caddyfile-config-form]")) {
            if (!(form instanceof HTMLFormElement)) {
                continue;
            }

            const caddyfileInput = form.elements.namedItem("caddyfile");
            const initialState = form.dataset.initialSerializedState || "";
            if (caddyfileInput instanceof HTMLTextAreaElement && caddyfileInput.value !== initialState) {
                return true;
            }
        }

        return false;
    };

    // Global list of SSE event listeners that modules can register
    const sseEventListeners = new Set();

    App.addSseEventListener = (listener) => {
        if (typeof listener === "function") {
            sseEventListeners.add(listener);
        }
    };

    App.removeSseEventListener = (listener) => {
        sseEventListeners.delete(listener);
    };

    App.initializeLiveUpdates = () => {
        const relevantResources = {
            "/caddyfile": ["caddyfile"],
            "/sites": ["site"],
            "/ssl-labs": ["ssllabs_scan"],
        };

        const currentPath = window.location.pathname;
        const segments = currentPath.split("/").filter(Boolean);
        const activeBaseSegment = segments.length > 0 ? `/${segments[0]}` : "/";
        const relevantTypes = new Set(relevantResources[activeBaseSegment] || []);
        // Allow SSE connection even if no reload types, for real-time UI updates (e.g., certificates)
        const hasRelevantTypes = relevantTypes.size > 0;
        if (App.liveUpdatesInitializedPath === activeBaseSegment) {
            return;
        }
        if (typeof App.liveUpdatesCleanup === "function") {
            App.liveUpdatesCleanup();
        }
        App.liveUpdatesInitializedPath = activeBaseSegment;

        let eventSource = null;
        let reconnectTimeoutId = null;
        let reloadTimeoutId = null;
        let reconnectAttempts = 0;
        let reloadPendingWhileHidden = false;
        let unsavedReloadNoticeShown = false;

        const clearReconnectTimeout = () => {
            if (reconnectTimeoutId !== null) {
                window.clearTimeout(reconnectTimeoutId);
                reconnectTimeoutId = null;
            }
        };

        const clearReloadTimeout = () => {
            if (reloadTimeoutId !== null) {
                window.clearTimeout(reloadTimeoutId);
                reloadTimeoutId = null;
            }
        };

        const triggerReload = () => {
            if (document.hidden) {
                reloadPendingWhileHidden = true;
                clearReloadTimeout();
                return;
            }
            if (App.hasUnsavedManagedFormChanges()) {
                reloadPendingWhileHidden = true;
                clearReloadTimeout();
                if (!unsavedReloadNoticeShown) {
                    App.pushInlineFlash?.("info", "External changes detected. Refresh after saving your current edits.");
                    unsavedReloadNoticeShown = true;
                }
                return;
            }
            if (reloadTimeoutId !== null) {
                return;
            }

            reloadTimeoutId = window.setTimeout(() => {
                reloadTimeoutId = null;
                reloadPendingWhileHidden = false;
                unsavedReloadNoticeShown = false;
                window.location.reload();
            }, 250);
        };

        const disconnect = () => {
            if (eventSource !== null) {
                eventSource.close();
                eventSource = null;
            }
            clearReconnectTimeout();
        };

        const connect = () => {
            if (document.hidden || eventSource !== null) {
                return;
            }

            eventSource = new EventSource(App.resolveSameOriginUrl("/api/v1/events"));
            eventSource.onopen = () => {
                reconnectAttempts = 0;
                clearReconnectTimeout();
            };

            const handleResourceEvent = (event) => {
                // Notify all registered listeners first
                for (const listener of sseEventListeners) {
                    try {
                        listener(event);
                    } catch {
                        // Ignore listener errors
                    }
                }

                // Then handle reload logic
                try {
                    const payload = JSON.parse(event.data);
                    if (hasRelevantTypes && relevantTypes.has(payload.type)) {
                        triggerReload();
                    }
                } catch {
                    // Ignore malformed event payloads.
                }
            };

            eventSource.onmessage = handleResourceEvent;
            eventSource.addEventListener("resource", handleResourceEvent);

            eventSource.onerror = () => {
                disconnect();
                if (document.hidden) {
                    return;
                }
                reconnectAttempts += 1;
                const delay = Math.min(1000 * Math.pow(2, reconnectAttempts - 1), 30000);
                reconnectTimeoutId = window.setTimeout(() => {
                    reconnectTimeoutId = null;
                    connect();
                }, delay);
            };
        };

        const handleVisibilityChange = () => {
            if (document.hidden) {
                if (reloadTimeoutId !== null) {
                    reloadPendingWhileHidden = true;
                    clearReloadTimeout();
                }
                disconnect();
                return;
            }
            if (reloadPendingWhileHidden) {
                triggerReload();
                return;
            }
            connect();
        };

        const handleBeforeUnload = () => {
            clearReloadTimeout();
            disconnect();
        };

        document.addEventListener("visibilitychange", handleVisibilityChange);
        window.addEventListener("beforeunload", handleBeforeUnload);

        App.liveUpdatesCleanup = () => {
            document.removeEventListener("visibilitychange", handleVisibilityChange);
            window.removeEventListener("beforeunload", handleBeforeUnload);
            clearReloadTimeout();
            disconnect();
            if (App.liveUpdatesCleanup) {
                App.liveUpdatesCleanup = null;
            }
            if (App.liveUpdatesInitializedPath === activeBaseSegment) {
                App.liveUpdatesInitializedPath = null;
            }
        };

        connect();
    };

    App.initializeMobileMenu = () => {
        const toggle = document.getElementById("mobileMenuToggle");
        const sidebar = document.getElementById("appSidebar");
        const backdrop = document.getElementById("sidebarBackdrop");
        const appContent = document.querySelector(".app-content");
        if (
            !(toggle instanceof HTMLButtonElement) ||
            !(sidebar instanceof HTMLElement) ||
            !(backdrop instanceof HTMLElement) ||
            toggle.dataset.mobileMenuInitialized === "true"
        ) {
            return;
        }
        toggle.dataset.mobileMenuInitialized = "true";

        const getFocusableSidebarElements = () => Array.from(
            sidebar.querySelectorAll("a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])")
        ).filter((element) => element instanceof HTMLElement && element.getClientRects().length > 0);

        const setPageInert = (enabled) => {
            if (appContent instanceof HTMLElement) {
                appContent.inert = enabled;
                if (enabled) {
                    appContent.setAttribute("aria-hidden", "true");
                } else {
                    appContent.removeAttribute("aria-hidden");
                }
            }
        };

        const closeMenu = (restoreFocus = false) => {
            sidebar.classList.remove("is-open");
            backdrop.classList.remove("is-visible");
            backdrop.setAttribute("aria-hidden", "true");
            toggle.classList.remove("is-active");
            toggle.setAttribute("aria-expanded", "false");
            toggle.setAttribute("aria-label", "Open menu");
            document.body.classList.remove("app-body--menu-open");
            setPageInert(false);
            if (restoreFocus) {
                toggle.focus();
            }
        };

        const openMenu = () => {
            sidebar.classList.add("is-open");
            backdrop.classList.add("is-visible");
            backdrop.setAttribute("aria-hidden", "false");
            toggle.classList.add("is-active");
            toggle.setAttribute("aria-expanded", "true");
            toggle.setAttribute("aria-label", "Close menu");
            document.body.classList.add("app-body--menu-open");
            setPageInert(true);

            const firstFocusable = getFocusableSidebarElements()[0];
            if (firstFocusable instanceof HTMLElement) {
                firstFocusable.focus();
            }
        };

        toggle.addEventListener("click", () => {
            if (sidebar.classList.contains("is-open")) {
                closeMenu();
            } else {
                openMenu();
            }
        });
        backdrop.addEventListener("click", () => closeMenu(true));
        sidebar.addEventListener("click", (event) => {
            if (!(event.target instanceof HTMLElement)) {
                return;
            }
            if (event.target.closest("a.app-nav__link, button[type='submit']") !== null) {
                closeMenu();
            }
        });
        document.addEventListener("keydown", (event) => {
            if (!sidebar.classList.contains("is-open")) {
                return;
            }

            if (event.key === "Escape") {
                closeMenu(true);
                return;
            }

            if (event.key !== "Tab") {
                return;
            }

            const focusable = getFocusableSidebarElements();
            if (focusable.length === 0) {
                event.preventDefault();
                toggle.focus();
                return;
            }

            const first = focusable[0];
            const last = focusable.at(-1);
            if (!(first instanceof HTMLElement) || !(last instanceof HTMLElement)) {
                return;
            }

            if (!sidebar.contains(document.activeElement)) {
                event.preventDefault();
                first.focus();
                return;
            }

            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        });

        const desktopMediaQuery = window.matchMedia("(min-width: 992px)");
        const handleDesktopChange = (event) => {
            if (event.matches) {
                closeMenu();
            }
        };

        if (typeof desktopMediaQuery.addEventListener === "function") {
            desktopMediaQuery.addEventListener("change", handleDesktopChange);
        } else if (typeof desktopMediaQuery.addListener === "function") {
            desktopMediaQuery.addListener(handleDesktopChange);
        }
    };

    App.initializeResponsiveCodeTextareas = () => {
        const textareas = Array.from(document.querySelectorAll("textarea[data-responsive-code-textarea]"))
            .filter((textarea) => textarea instanceof HTMLTextAreaElement);
        if (textareas.length === 0 || document.body.dataset.responsiveCodeTextareasInitialized === "true") {
            return;
        }
        document.body.dataset.responsiveCodeTextareasInitialized = "true";

        const mobileMediaQuery = window.matchMedia("(max-width: 767.98px)");
        const applyTextareaWrapMode = () => {
            for (const textarea of textareas) {
                textarea.setAttribute("wrap", mobileMediaQuery.matches ? "soft" : "off");
            }
        };

        applyTextareaWrapMode();
        if (typeof mobileMediaQuery.addEventListener === "function") {
            mobileMediaQuery.addEventListener("change", applyTextareaWrapMode);
        } else if (typeof mobileMediaQuery.addListener === "function") {
            mobileMediaQuery.addListener(applyTextareaWrapMode);
        }
    };

    App.initializeLoadingSubmitForms = () => {
        for (const form of document.querySelectorAll("form[data-loading-submit-form]")) {
            if (!(form instanceof HTMLFormElement) || form.dataset.loadingSubmitInitialized === "true") {
                continue;
            }
            form.dataset.loadingSubmitInitialized = "true";

            form.addEventListener("submit", (event) => {
                if (event.defaultPrevented) {
                    return;
                }

                const submitter = event.submitter;
                if (!(submitter instanceof HTMLElement)) {
                    return;
                }

                const button = submitter.matches("[data-loading-submit-button]")
                    ? submitter
                    : submitter.closest("[data-loading-submit-button]");
                if (!(button instanceof HTMLElement) || ("disabled" in button && button.disabled)) {
                    return;
                }

                const spinner = button.querySelector("[data-loading-submit-spinner]");
                const label = button.querySelector("[data-loading-submit-label]");
                const loadingText = button.dataset.loadingLabel || button.textContent?.trim() || "Processing...";

                if ("disabled" in button) {
                    button.disabled = true;
                }
                button.setAttribute("aria-disabled", "true");
                button.setAttribute("aria-busy", "true");

                if (spinner instanceof HTMLElement) {
                    spinner.classList.remove("d-none");
                }
                if (label instanceof HTMLElement) {
                    label.textContent = loadingText;
                } else if (button instanceof HTMLInputElement && button.type === "submit") {
                    button.value = loadingText;
                }
            });
        }
    };

    App.initializeAutoDismissToasts = () => {
        for (const toastElement of document.querySelectorAll("[data-auto-dismiss-toast]")) {
            if (!(toastElement instanceof HTMLElement) || toastElement.dataset.autoDismissInitialized === "true") {
                continue;
            }
            toastElement.dataset.autoDismissInitialized = "true";
            toastElement.classList.add("fade");

            const delayValue = Number.parseInt(toastElement.dataset.autoDismissDelay || "5000", 10);
            const delay = Number.isFinite(delayValue) && delayValue > 0
                ? Math.min(delayValue, 60000)
                : 5000;

            if (window.bootstrap?.Toast) {
                const toast = window.bootstrap.Toast.getOrCreateInstance(toastElement, {
                    autohide: true,
                    delay,
                });
                toastElement.addEventListener("hidden.bs.toast", () => toastElement.remove(), { once: true });
                toast.show();
                continue;
            }

            toastElement.classList.add("show");
            window.setTimeout(() => {
                toastElement.classList.add("showing");
                toastElement.classList.remove("show");
                window.setTimeout(() => toastElement.remove(), 180);
            }, delay);
        }
    };

    App.ensureToastStack = () => {
        const existing = document.querySelector(".app-toast-stack");
        if (existing instanceof HTMLElement) {
            return existing;
        }
        const appContent = document.querySelector(".app-content") || document.body;
        if (!(appContent instanceof HTMLElement)) {
            return null;
        }
        const toastStack = document.createElement("div");
        toastStack.className = "toast-container app-toast-stack position-fixed top-0 end-0 p-3";
        toastStack.setAttribute("aria-live", "polite");
        toastStack.setAttribute("aria-atomic", "true");
        appContent.append(toastStack);
        return toastStack;
    };

    App.pushInlineFlash = (category, message) => {
        const toastStack = App.ensureToastStack();
        if (!(toastStack instanceof HTMLElement)) {
            return;
        }

        const safeCategory = App.allowedFlashCategories.has(category) ? category : "info";
        const isAlertFlash = safeCategory === "danger" || safeCategory === "warning";

        const toastElement = document.createElement("div");
        toastElement.className = `toast align-items-center text-bg-${safeCategory} border-0 shadow-sm`;
        toastElement.setAttribute("role", isAlertFlash ? "alert" : "status");
        toastElement.setAttribute("aria-live", isAlertFlash ? "assertive" : "polite");
        toastElement.setAttribute("aria-atomic", "true");
        toastElement.setAttribute("data-auto-dismiss-toast", "");
        toastElement.setAttribute("data-auto-dismiss-delay", isAlertFlash ? "12000" : "5000");

        const content = document.createElement("div");
        content.className = "d-flex";

        const body = document.createElement("div");
        body.className = "toast-body";
        body.textContent = message;

        const closeButton = document.createElement("button");
        closeButton.type = "button";
        closeButton.className = "btn-close btn-close-white me-2 m-auto";
        closeButton.setAttribute("data-bs-dismiss", "toast");
        closeButton.setAttribute("aria-label", "Close");
        closeButton.addEventListener("click", () => {
            toastElement.remove();
        });

        content.append(body, closeButton);
        toastElement.append(content);

        toastStack.prepend(toastElement);
        App.initializeAutoDismissToasts();
    };

    const confirmModalElement = document.getElementById("confirmActionModal");
    const confirmModalTitleElement = document.getElementById("confirmActionModalLabel");
    const confirmModalMessageElement = document.getElementById("confirmActionModalMessage");
    const confirmModalAcceptButton = document.getElementById("confirmActionModalAccept");
    const defaultConfirmAcceptClass = "btn btn-primary";

    let confirmActionModal = null;
    let pendingConfirmElement = null;
    let previousActiveConfirmElement = null;

    const getConfirmActionModal = () => {
        if (!(confirmModalElement instanceof HTMLElement) || !window.bootstrap?.Modal) {
            return null;
        }
        if (confirmActionModal === null) {
            confirmActionModal = new window.bootstrap.Modal(confirmModalElement);
        }
        return confirmActionModal;
    };

    const resetPendingConfirm = () => {
        pendingConfirmElement = null;
        previousActiveConfirmElement = null;
        if (confirmModalTitleElement instanceof HTMLElement) {
            confirmModalTitleElement.textContent = "Confirm action";
        }
        if (confirmModalMessageElement instanceof HTMLElement) {
            confirmModalMessageElement.textContent = "Continue?";
        }
        if (confirmModalAcceptButton instanceof HTMLButtonElement) {
            confirmModalAcceptButton.disabled = false;
            confirmModalAcceptButton.textContent = "Continue";
            confirmModalAcceptButton.className = defaultConfirmAcceptClass;
        }
    };

    const isSubmitControl = (element) => {
        if (element instanceof HTMLButtonElement) {
            return element.type === "submit" || element.type === "";
        }
        if (element instanceof HTMLInputElement) {
            return element.type === "submit" || element.type === "image";
        }
        return false;
    };

    const isDisabledConfirmTarget = (element) => {
        if (!(element instanceof HTMLElement)) {
            return true;
        }
        if (element.getAttribute("aria-disabled") === "true") {
            return true;
        }
        if ("disabled" in element && element.disabled === true) {
            return true;
        }
        return element.classList.contains("disabled");
    };

    const submitConfirmedElement = () => {
        const target = pendingConfirmElement;
        if (!(target instanceof HTMLElement)) {
            return;
        }

        if (confirmModalAcceptButton instanceof HTMLButtonElement) {
            confirmModalAcceptButton.disabled = true;
        }

        try {
            if (target instanceof HTMLAnchorElement && target.href) {
                window.location.assign(target.href);
                return;
            }

            const form = target.closest("form");
            if (!(form instanceof HTMLFormElement)) {
                return;
            }

            if (isSubmitControl(target)) {
                form.requestSubmit(target);
                return;
            }

            form.requestSubmit();
        } finally {
            pendingConfirmElement = null;
            if (confirmModalAcceptButton instanceof HTMLButtonElement) {
                confirmModalAcceptButton.disabled = false;
            }
        }
    };

    if (confirmModalElement instanceof HTMLElement) {
        confirmModalElement.addEventListener("hidden.bs.modal", () => {
            const focusTarget = previousActiveConfirmElement;
            resetPendingConfirm();
            if (focusTarget instanceof HTMLElement && focusTarget.isConnected) {
                focusTarget.focus();
            }
        });
    }

    if (confirmModalAcceptButton instanceof HTMLButtonElement) {
        confirmModalAcceptButton.addEventListener("click", () => {
            const modal = getConfirmActionModal();
            modal?.hide();
            submitConfirmedElement();
        });
    }

    document.addEventListener("click", (event) => {
        if (!(event.target instanceof Element)) {
            return;
        }
        const button = event.target.closest(".js-confirm");
        if (!(button instanceof HTMLElement) || isDisabledConfirmTarget(button)) {
            return;
        }

        event.preventDefault();
        event.stopPropagation();

        const modal = getConfirmActionModal();
        if (modal === null) {
            const message = button.getAttribute("data-confirm") || "Continue?";
            if (!window.confirm(message)) {
                return;
            }

            if (button instanceof HTMLAnchorElement && button.href) {
                window.location.assign(button.href);
                return;
            }
            const form = button.closest("form");
            if (form instanceof HTMLFormElement) {
                form.requestSubmit(isSubmitControl(button) ? button : undefined);
            }
            return;
        }

        pendingConfirmElement = button;
        previousActiveConfirmElement = button instanceof HTMLElement
            ? button
            : (document.activeElement instanceof HTMLElement ? document.activeElement : null);

        if (confirmModalTitleElement instanceof HTMLElement) {
            confirmModalTitleElement.textContent = button.getAttribute("data-confirm-title") || button.textContent?.trim() || "Confirm action";
        }
        if (confirmModalMessageElement instanceof HTMLElement) {
            confirmModalMessageElement.textContent = button.getAttribute("data-confirm") || "Continue?";
        }
        if (confirmModalAcceptButton instanceof HTMLButtonElement) {
            const requestedButtonClass = button.getAttribute("data-confirm-btn-class") || "btn-primary";
            const confirmButtonClass = App.allowedConfirmButtonClasses.has(requestedButtonClass)
                ? requestedButtonClass
                : "btn-primary";
            confirmModalAcceptButton.className = `btn ${confirmButtonClass}`;
            confirmModalAcceptButton.textContent = button.getAttribute("data-confirm-accept") || button.textContent?.trim() || "Continue";
        }

        modal.show();
    });
})();
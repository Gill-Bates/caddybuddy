//
// app/static/js/app.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

"use strict";

const allowedConfirmButtonClasses = new Set([
    "btn-primary",
    "btn-secondary",
    "btn-danger",
    "btn-warning",
]);

const allowedFlashCategories = new Set([
    "success",
    "danger",
    "warning",
    "info",
    "primary",
    "secondary",
]);

const readCsrfToken = (form = null) => {
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

const resolveSameOriginUrl = (rawUrl) => {
    const url = new URL(rawUrl, window.location.origin);
    if (url.origin !== window.location.origin) {
        throw new Error("External validate URLs are not allowed.");
    }
    return url.toString();
};

const initializeLiveUpdates = () => {
    const relevantResources = {
        "/caddyfile": ["caddyfile"],
        "/sites": ["site"],
        "/ssl-labs": ["ssllabs_scan"],
    };

    const currentPath = window.location.pathname;
    const segments = currentPath.split("/").filter(Boolean);
    const activeBaseSegment = segments.length > 0 ? `/${segments[0]}` : "/";
    const relevantTypes = new Set(relevantResources[activeBaseSegment] || []);
    if (relevantTypes.size === 0) {
        return;
    }

    let eventSource = null;
    let reconnectTimeoutId = null;
    let reloadTimeoutId = null;
    let reconnectAttempts = 0;
    let reloadPendingWhileHidden = false;

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
        if (reloadTimeoutId !== null) {
            return;
        }

        reloadTimeoutId = window.setTimeout(() => {
            reloadTimeoutId = null;
            reloadPendingWhileHidden = false;
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

        eventSource = new EventSource("/api/v1/events");
        eventSource.onopen = () => {
            reconnectAttempts = 0;
            clearReconnectTimeout();
        };

        const handleResourceEvent = (event) => {
            try {
                const payload = JSON.parse(event.data);
                if (relevantTypes.has(payload.type)) {
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

    document.addEventListener("visibilitychange", () => {
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
    });

    window.addEventListener("beforeunload", () => {
        clearReloadTimeout();
        disconnect();
    });

    connect();
};

const initializeMobileMenu = () => {
    const toggle = document.getElementById("mobileMenuToggle");
    const sidebar = document.getElementById("appSidebar");
    const backdrop = document.getElementById("sidebarBackdrop");
    if (
        !(toggle instanceof HTMLButtonElement) ||
        !(sidebar instanceof HTMLElement) ||
        !(backdrop instanceof HTMLElement)
    ) {
        return;
    }

    const closeMenu = () => {
        sidebar.classList.remove("is-open");
        backdrop.classList.remove("is-visible");
        toggle.classList.remove("is-active");
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-label", "Open menu");
        document.body.classList.remove("app-body--menu-open");
    };

    const openMenu = () => {
        sidebar.classList.add("is-open");
        backdrop.classList.add("is-visible");
        toggle.classList.add("is-active");
        toggle.setAttribute("aria-expanded", "true");
        toggle.setAttribute("aria-label", "Close menu");
        document.body.classList.add("app-body--menu-open");
    };

    toggle.addEventListener("click", () => {
        if (sidebar.classList.contains("is-open")) {
            closeMenu();
        } else {
            openMenu();
        }
    });
    backdrop.addEventListener("click", closeMenu);
    sidebar.addEventListener("click", (event) => {
        if (!(event.target instanceof HTMLElement)) {
            return;
        }
        if (event.target.closest("a.app-nav__link, button[type='submit']") !== null) {
            closeMenu();
        }
    });
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && sidebar.classList.contains("is-open")) {
            closeMenu();
            toggle.focus();
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

const initializeLoadingSubmitForms = () => {
    for (const form of document.querySelectorAll("form[data-loading-submit-form]")) {
        if (!(form instanceof HTMLFormElement)) {
            continue;
        }

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

const initializeAutoDismissAlerts = () => {
    for (const alertElement of document.querySelectorAll("[data-auto-dismiss-alert]")) {
        if (!(alertElement instanceof HTMLElement) || alertElement.dataset.autoDismissInitialized === "true") {
            continue;
        }
        alertElement.dataset.autoDismissInitialized = "true";

        const delayValue = Number.parseInt(alertElement.dataset.autoDismissDelay || "5000", 10);
        const delay = Number.isFinite(delayValue) && delayValue > 0
            ? Math.min(delayValue, 60000)
            : 5000;
        let timeoutId = null;

        const clearDismissTimeout = () => {
            if (timeoutId !== null) {
                window.clearTimeout(timeoutId);
                timeoutId = null;
            }
        };

        const dismissAlert = () => {
            clearDismissTimeout();
            if (window.bootstrap?.Alert) {
                window.bootstrap.Alert.getOrCreateInstance(alertElement).close();
                return;
            }
            alertElement.classList.remove("show");
            window.setTimeout(() => alertElement.remove(), 150);
        };

        const scheduleDismiss = () => {
            clearDismissTimeout();
            timeoutId = window.setTimeout(dismissAlert, delay);
        };

        alertElement.addEventListener("mouseenter", clearDismissTimeout);
        alertElement.addEventListener("mouseleave", scheduleDismiss);
        alertElement.addEventListener("focusin", clearDismissTimeout);
        alertElement.addEventListener("focusout", scheduleDismiss);
        alertElement.addEventListener("closed.bs.alert", clearDismissTimeout);
        scheduleDismiss();
    }
};

const ensureFlashStack = () => {
    const existing = document.querySelector(".app-flash-stack");
    if (existing instanceof HTMLElement) {
        return existing;
    }
    const appContainer = document.querySelector(".app-container");
    if (!(appContainer instanceof HTMLElement)) {
        return null;
    }
    const flashStack = document.createElement("div");
    flashStack.className = "app-flash-stack";
    appContainer.prepend(flashStack);
    return flashStack;
};

const pushInlineFlash = (category, message) => {
    const flashStack = ensureFlashStack();
    if (!(flashStack instanceof HTMLElement)) {
        return;
    }

    const safeCategory = allowedFlashCategories.has(category) ? category : "info";

    const alertElement = document.createElement("div");
    alertElement.className = `alert alert-${safeCategory} alert-dismissible fade show shadow-sm border-0`;
    alertElement.setAttribute("role", "alert");
    alertElement.setAttribute("data-auto-dismiss-alert", "");
    alertElement.setAttribute("data-auto-dismiss-delay", "5000");
    alertElement.textContent = message;

    const closeButton = document.createElement("button");
    closeButton.type = "button";
    closeButton.className = "btn-close";
    closeButton.setAttribute("data-bs-dismiss", "alert");
    closeButton.setAttribute("aria-label", "Close");
    alertElement.append(closeButton);

    flashStack.prepend(alertElement);
    initializeAutoDismissAlerts();
};

const domainTokenPattern = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

const normalizeDomainToken = (value) => {
    const normalized = String(value || "").trim().toLowerCase().replace(/\.+$/u, "");
    if (!normalized) {
        return null;
    }
    if (normalized.length > 253 || !domainTokenPattern.test(normalized)) {
        return null;
    }
    return normalized;
};

const splitDomainTokenInput = (value) => String(value || "")
    .split(/[\s,]+/u)
    .map((token) => token.trim())
    .filter((token) => token.length > 0);

const initializeSiteDomainInputs = () => {
    for (const container of document.querySelectorAll("[data-domain-tag-input]")) {
        if (!(container instanceof HTMLElement) || container.dataset.domainTagInitialized === "true") {
            continue;
        }
        container.dataset.domainTagInitialized = "true";

        const hiddenInput = container.querySelector("input[name='domain']");
        const entryInput = container.querySelector("[data-domain-tag-entry]");
        const tagList = container.querySelector("[data-domain-tag-list]");
        const errorElement = container.querySelector("[data-domain-tag-error]");
        if (
            !(hiddenInput instanceof HTMLInputElement) ||
            !(entryInput instanceof HTMLInputElement) ||
            !(tagList instanceof HTMLElement) ||
            !(errorElement instanceof HTMLElement)
        ) {
            continue;
        }

        const selectedSiteId = Number.parseInt(container.dataset.selectedSiteId || "", 10);
        const rawCatalog = container.dataset.existingDomains || "[]";
        let existingDomains = [];
        try {
            existingDomains = JSON.parse(rawCatalog);
        } catch {
            existingDomains = [];
        }

        const blockedDomains = new Set();
        for (const item of existingDomains) {
            if (!item || typeof item !== "object") {
                continue;
            }
            const itemSiteId = Number.parseInt(String(item.site_id || ""), 10);
            if (Number.isFinite(selectedSiteId) && itemSiteId === selectedSiteId) {
                continue;
            }

            for (const token of splitDomainTokenInput(item.domain)) {
                const normalized = normalizeDomainToken(token);
                if (normalized) {
                    blockedDomains.add(normalized);
                }
            }
        }

        let domains = [...new Set(splitDomainTokenInput(hiddenInput.value)
            .map((token) => normalizeDomainToken(token))
            .filter((token) => token !== null))];

        const updateHiddenValue = () => {
            hiddenInput.value = domains.join(", ");
            hiddenInput.dispatchEvent(new Event("input", { bubbles: true }));
            hiddenInput.dispatchEvent(new Event("change", { bubbles: true }));
        };

        const setError = (message) => {
            entryInput.setCustomValidity(message);
            if (message) {
                errorElement.textContent = message;
                errorElement.classList.remove("d-none");
            } else {
                errorElement.textContent = "";
                errorElement.classList.add("d-none");
            }
        };

        const renderTags = () => {
            tagList.replaceChildren();
            for (const domainName of domains) {
                const token = document.createElement("span");
                token.className = "tag-input__token";
                token.textContent = domainName;

                const removeButton = document.createElement("button");
                removeButton.type = "button";
                removeButton.className = "tag-input__remove";
                removeButton.setAttribute("aria-label", `Remove ${domainName}`);
                removeButton.textContent = "×";
                removeButton.addEventListener("click", () => {
                    domains = domains.filter((value) => value !== domainName);
                    setError("");
                    renderTags();
                    updateHiddenValue();
                    entryInput.focus();
                });

                token.append(removeButton);
                tagList.append(token);
            }
        };

        const addDomains = (rawValue) => {
            const tokens = splitDomainTokenInput(rawValue);
            if (tokens.length === 0) {
                return false;
            }

            let changed = false;
            for (const token of tokens) {
                const normalized = normalizeDomainToken(token);
                if (!normalized) {
                    setError(`'${token}' is not a valid domain.`);
                    return changed;
                }
                if (blockedDomains.has(normalized)) {
                    setError(`'${normalized}' is already assigned to another site configuration.`);
                    return changed;
                }
                if (domains.includes(normalized)) {
                    continue;
                }
                domains.push(normalized);
                changed = true;
            }

            if (changed) {
                setError("");
                renderTags();
                updateHiddenValue();
            }
            return changed;
        };

        entryInput.addEventListener("keydown", (event) => {
            if (["Enter", "Tab", ",", " "].includes(event.key)) {
                if (entryInput.value.trim() === "") {
                    return;
                }
                event.preventDefault();
                const changed = addDomains(entryInput.value);
                if (changed) {
                    entryInput.value = "";
                }
                return;
            }

            if (event.key === "Backspace" && entryInput.value === "" && domains.length > 0) {
                domains = domains.slice(0, -1);
                setError("");
                renderTags();
                updateHiddenValue();
            }
        });

        entryInput.addEventListener("blur", () => {
            if (entryInput.value.trim() === "") {
                return;
            }
            const changed = addDomains(entryInput.value);
            if (changed) {
                entryInput.value = "";
            }
        });

        entryInput.addEventListener("paste", (event) => {
            const pastedText = event.clipboardData?.getData("text") || "";
            if (!/[\s,]/u.test(pastedText)) {
                return;
            }
            event.preventDefault();
            const changed = addDomains(pastedText);
            if (changed) {
                entryInput.value = "";
            }
        });

        entryInput.addEventListener("input", () => {
            if (entryInput.validity.customError) {
                setError("");
            }
        });

        renderTags();
        updateHiddenValue();
    }
};

const serializeSiteConfigFormState = (form) => {
    const formData = new FormData(form);
    const entries = [];

    for (const [key, value] of formData.entries()) {
        if (key === "csrf_token") {
            continue;
        }
        if (value instanceof File) {
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

const siteConfigFormHasRequiredValues = (form) => {
    const domainInput = form.elements.namedItem("domain");
    const directivesInput = form.elements.namedItem("caddy_directives");

    if (!(domainInput instanceof HTMLInputElement) || domainInput.value.trim() === "") {
        return false;
    }
    if (!(directivesInput instanceof HTMLTextAreaElement) || directivesInput.value.trim() === "") {
        return false;
    }
    return form.checkValidity();
};

const caddyfileFormHasRequiredValues = (form) => {
    const caddyfileInput = form.elements.namedItem("caddyfile");
    if (!(caddyfileInput instanceof HTMLTextAreaElement)) {
        return false;
    }
    return caddyfileInput.value.trim() !== "";
};

const setButtonInteractionState = (button, enabled) => {
    if (!(button instanceof HTMLButtonElement)) {
        return;
    }
    button.disabled = !enabled;
    button.setAttribute("aria-disabled", enabled ? "false" : "true");
};

const updateSiteConfigFormActions = (form) => {
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-site-config-form")) {
        return;
    }

    const validateButton = form.querySelector("[data-validate-form-button]");
    const saveButton = form.querySelector("[data-site-save-button]");
    const currentState = serializeSiteConfigFormState(form);
    const initialState = form.dataset.initialSerializedState || "";
    const lastValidatedState = form.dataset.lastValidatedState || "";

    if (form.dataset.validationState === "valid" && currentState !== lastValidatedState) {
        form.dataset.validationState = "unvalidated";
    }

    const hasChanges = currentState !== initialState;
    const hasRequiredValues = siteConfigFormHasRequiredValues(form);
    const validationMatchesCurrentState = form.dataset.validationState === "valid" && currentState === lastValidatedState;

    setButtonInteractionState(validateButton, hasChanges && hasRequiredValues);
    setButtonInteractionState(saveButton, hasChanges && hasRequiredValues && validationMatchesCurrentState);
};

const updateCaddyfileFormActions = (form) => {
    if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-caddyfile-config-form")) {
        return;
    }

    const validateButton = form.querySelector("[data-validate-form-button]");
    const saveButton = form.querySelector("#caddyfile-save-btn");
    const locked = form.dataset.caddyfileLocked === "true";

    if (locked) {
        setButtonInteractionState(validateButton, false);
        setButtonInteractionState(saveButton, false);
        return;
    }

    setButtonInteractionState(validateButton, caddyfileFormHasRequiredValues(form));
};

const initializeSiteConfigForms = () => {
    for (const form of document.querySelectorAll("form[data-site-config-form]")) {
        if (!(form instanceof HTMLFormElement) || form.dataset.siteConfigInitialized === "true") {
            continue;
        }
        form.dataset.siteConfigInitialized = "true";
        form.dataset.initialSerializedState = serializeSiteConfigFormState(form);
        form.dataset.lastValidatedState = "";
        form.dataset.validationState = "unvalidated";

        const syncFormState = () => {
            updateSiteConfigFormActions(form);
        };

        form.addEventListener("input", syncFormState);
        form.addEventListener("change", syncFormState);
        form.addEventListener("submit", (event) => {
            const submitter = event.submitter;
            if (!(submitter instanceof HTMLElement)) {
                return;
            }

            const saveButton = submitter.matches("[data-site-save-button]")
                ? submitter
                : submitter.closest("[data-site-save-button]");
            if (!(saveButton instanceof HTMLButtonElement)) {
                return;
            }

            updateSiteConfigFormActions(form);
            if (!saveButton.disabled) {
                return;
            }

            event.preventDefault();
            pushInlineFlash("warning", "Validate the current site configuration before saving.");
        });

        syncFormState();
    }
};

const initializeCaddyfileForms = () => {
    for (const form of document.querySelectorAll("form[data-caddyfile-config-form]")) {
        if (!(form instanceof HTMLFormElement) || form.dataset.caddyfileConfigInitialized === "true") {
            continue;
        }
        form.dataset.caddyfileConfigInitialized = "true";

        const syncFormState = () => {
            updateCaddyfileFormActions(form);
        };

        form.addEventListener("input", syncFormState);
        form.addEventListener("change", syncFormState);
        syncFormState();
    }
};

const initializeValidateButtons = () => {
    for (const button of document.querySelectorAll("[data-validate-form-button]")) {
        if (!(button instanceof HTMLButtonElement) || button.dataset.validateInitialized === "true") {
            continue;
        }
        button.dataset.validateInitialized = "true";

        button.addEventListener("click", async (event) => {
            event.preventDefault();

            const form = button.closest("form");
            const validateUrl = button.dataset.validateUrl;
            if (!(form instanceof HTMLFormElement) || !validateUrl) {
                return;
            }

            let requestUrl;
            try {
                requestUrl = resolveSameOriginUrl(validateUrl);
            } catch {
                pushInlineFlash("danger", "Invalid validation URL.");
                return;
            }

            const spinner = button.querySelector("[data-validate-button-spinner]");
            const label = button.querySelector("[data-validate-button-label]");
            const originalLabel = label instanceof HTMLElement ? label.textContent : button.textContent;
            const submittedState = serializeSiteConfigFormState(form);
            const headers = new Headers({ "X-Requested-With": "fetch" });
            const csrfToken = readCsrfToken(form);
            if (csrfToken) {
                headers.set("X-CSRF-Token", csrfToken);
            }

            button.disabled = true;
            button.setAttribute("aria-disabled", "true");
            button.setAttribute("aria-busy", "true");
            if (spinner instanceof HTMLElement) {
                spinner.classList.remove("d-none");
            }
            if (label instanceof HTMLElement) {
                label.textContent = "Validating...";
            }

            try {
                const response = await fetch(requestUrl, {
                    method: "POST",
                    body: new FormData(form),
                    credentials: "same-origin",
                    headers,
                });
                const contentType = response.headers.get("content-type") || "";
                let payload = {};

                if (contentType.includes("application/json")) {
                    payload = await response.json();
                } else {
                    payload = { message: (await response.text()).trim() };
                }

                const successPrefix = button.dataset.validateSuccessPrefix || "Validation successful";
                const errorPrefix = button.dataset.validateErrorPrefix || "Validation failed";

                if (response.ok && payload.valid) {
                    form.dataset.lastValidatedState = submittedState;
                    form.dataset.validationState = "valid";
                    pushInlineFlash("success", `${successPrefix}: ${payload.message}`);
                } else {
                    form.dataset.validationState = "invalid";
                    const message = typeof payload.message === "string" && payload.message
                        ? payload.message
                        : "Unknown validation error.";
                    pushInlineFlash("danger", `${errorPrefix}: ${message}`);
                }
            } catch {
                form.dataset.validationState = "invalid";
                pushInlineFlash("danger", "Validation request failed. Please try again.");
            } finally {
                button.disabled = false;
                button.setAttribute("aria-disabled", "false");
                button.removeAttribute("aria-busy");
                if (spinner instanceof HTMLElement) {
                    spinner.classList.add("d-none");
                }
                if (label instanceof HTMLElement) {
                    label.textContent = originalLabel || "Validate";
                }
                updateSiteConfigFormActions(form);
                updateCaddyfileFormActions(form);
            }
        });
    }
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
        confirmModalAcceptButton.textContent = "Continue";
        confirmModalAcceptButton.className = defaultConfirmAcceptClass;
    }
};

const submitConfirmedElement = () => {
    if (!(pendingConfirmElement instanceof HTMLElement)) {
        return;
    }
    const target = pendingConfirmElement;
    resetPendingConfirm();

    if (target instanceof HTMLAnchorElement && target.href) {
        window.location.assign(target.href);
        return;
    }

    const form = target.closest("form");
    if (!(form instanceof HTMLFormElement)) {
        return;
    }
    if (target instanceof HTMLButtonElement || target instanceof HTMLInputElement) {
        form.requestSubmit(target);
        return;
    }
    form.requestSubmit();
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
    if (button === null) {
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
            form.requestSubmit(button instanceof HTMLButtonElement || button instanceof HTMLInputElement ? button : undefined);
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
        const confirmButtonClass = allowedConfirmButtonClasses.has(requestedButtonClass)
            ? requestedButtonClass
            : "btn-primary";
        confirmModalAcceptButton.className = `btn ${confirmButtonClass}`;
        confirmModalAcceptButton.textContent = button.getAttribute("data-confirm-accept") || button.textContent?.trim() || "Continue";
    }

    modal.show();
});

initializeLiveUpdates();
initializeMobileMenu();
initializeLoadingSubmitForms();
initializeAutoDismissAlerts();
initializeSiteDomainInputs();
initializeSiteConfigForms();
initializeCaddyfileForms();
initializeValidateButtons();
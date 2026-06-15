//
// app/static/js/app-forms.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

(() => {
    "use strict";

    const App = window.CaddyBuddyApp || (window.CaddyBuddyApp = {});
    const settingsEmailPattern = /^[^@\s]+@[^@\s]+\.[^@\s]+$/u;
    const passwordPolicyElement = document.querySelector("[data-password-policy]");
    const parsedMinimumPasswordLength = Number.parseInt(passwordPolicyElement?.dataset.passwordPolicyMinLength || "", 10);
    const minimumPasswordLength = Number.isInteger(parsedMinimumPasswordLength) && parsedMinimumPasswordLength > 0
        ? parsedMinimumPasswordLength
        : 8;
    const passwordPolicyMessage = passwordPolicyElement?.dataset.passwordPolicyMessage
        || `Password must be at least ${minimumPasswordLength} characters long and contain uppercase, lowercase, digit, and special character.`;

    const isAbortError = (error) => error instanceof DOMException && error.name === "AbortError";

    const fetchWithTimeout = async (rawUrl, options = {}, timeoutMs = 10000) => {
        if (typeof window.AbortController !== "function") {
            return fetch(rawUrl, options);
        }

        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
        try {
            return await fetch(rawUrl, {
                ...options,
                signal: controller.signal,
            });
        } finally {
            window.clearTimeout(timeoutId);
        }
    };

    const setFieldFeedbackState = (field) => {
        if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement)) {
            return;
        }

        const hasValue = field instanceof HTMLInputElement && field.type === "checkbox"
            ? true
            : field.value.trim() !== "";
        const isValid = field.checkValidity();

        field.classList.toggle("is-invalid", !isValid);
        field.classList.toggle("is-valid", hasValue && isValid && field.dataset.serverValid === "true");
        field.setAttribute("aria-invalid", isValid ? "false" : "true");
    };

    const validateSettingsField = (field) => {
        if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement)) {
            return true;
        }

        let message = "";
        const rawValue = field.value;
        const normalizedValue = rawValue.trim();

        if (field.name === "caddy_api_url") {
            if (!normalizedValue) {
                message = "Caddy API URL cannot be empty";
            } else {
                try {
                    const parsed = new URL(normalizedValue);
                    if (!["http:", "https:"].includes(parsed.protocol)) {
                        message = "caddy_api_url must use http or https.";
                    } else if (parsed.username || parsed.password) {
                        message = "caddy_api_url must not include username or password.";
                    } else if (!parsed.hostname) {
                        message = "caddy_api_url must include a host.";
                    } else if (parsed.pathname && parsed.pathname !== "/") {
                        message = "caddy_api_url must not include a path.";
                    } else if (parsed.search) {
                        message = "caddy_api_url must not include query or fragment.";
                    } else if (parsed.hash) {
                        message = "caddy_api_url must not include query or fragment.";
                    } else if (!parsed.port) {
                        message = "caddy_api_url must include an explicit port (for example :2019).";
                    } else {
                        const port = Number.parseInt(parsed.port, 10);
                        if (!Number.isInteger(port) || port < 1 || port > 65535) {
                            message = "caddy_api_url has an invalid port.";
                        }
                    }
                } catch {
                    message = "caddy_api_url must use http or https.";
                }
            }
        } else if (field.name === "caddyfile_path") {
            if (!normalizedValue) {
                message = "Caddyfile path cannot be empty";
            } else if (normalizedValue.includes("\0")) {
                message = "Caddyfile path contains an invalid character.";
            } else if (!normalizedValue.startsWith("/")) {
                message = "Caddyfile path must be absolute.";
            }
        } else if (field.name === "ssllabs_email") {
            if (normalizedValue && !settingsEmailPattern.test(normalizedValue.toLowerCase())) {
                message = "ssllabs_email must be a valid email address.";
            }
        } else if (field.name === "new_password" && rawValue !== "") {
            const hasLowercase = /[a-z]/u.test(rawValue);
            const hasUppercase = /[A-Z]/u.test(rawValue);
            const hasDigit = /\d/u.test(rawValue);
            const hasSpecial = /[^\p{L}\d]/u.test(rawValue);

            if (rawValue.length < minimumPasswordLength) {
                message = passwordPolicyMessage;
            } else if (!(hasLowercase && hasUppercase && hasDigit && hasSpecial)) {
                message = passwordPolicyMessage;
            }
        }

        field.setCustomValidity(message);
        setFieldFeedbackState(field);
        return message === "" && field.checkValidity();
    };

    const probeCaddySettingsValidity = async (caddyApiUrlField, otherFields) => {
        try {
            const response = await fetchWithTimeout(
                App.resolveSameOriginUrl("/api/caddy/status"),
                {
                    credentials: "same-origin",
                    headers: {
                        Accept: "application/json",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                },
                5000
            );
            if (!response.ok) {
                return;
            }
            const payload = await response.json();

            if (payload.admin_api_reachable === true && caddyApiUrlField instanceof HTMLInputElement) {
                caddyApiUrlField.dataset.serverValid = "true";
                validateSettingsField(caddyApiUrlField);
            }
            for (const field of otherFields) {
                field.dataset.serverValid = "true";
                validateSettingsField(field);
            }
        } catch {
            // Probe failed — fields stay without is-valid
        }
    };

    App.initializeSettingsAutoSave = () => {
        const getFieldValue = (field) => {
            if (field instanceof HTMLInputElement) {
                if (field.type === "checkbox" || field.type === "radio") {
                    return field.checked ? "true" : "false";
                }
                return field.value;
            }
            if (field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement) {
                return field.value;
            }
            return "";
        };

        const snapshotFields = (fields) => JSON.stringify(
            fields.map((field) => [field.name, getFieldValue(field)])
        );

        const updateStatusElement = (statusElement, message, tone = "muted") => {
            if (!(statusElement instanceof HTMLElement)) {
                return;
            }
            statusElement.textContent = message;
            statusElement.classList.remove("text-body-secondary", "text-success", "text-danger");
            if (tone === "success") {
                statusElement.classList.add("text-success");
                return;
            }
            if (tone === "danger") {
                statusElement.classList.add("text-danger");
                return;
            }
            statusElement.classList.add("text-body-secondary");
        };

        for (const form of document.querySelectorAll("form[data-auto-save-form]")) {
            if (!(form instanceof HTMLFormElement) || form.dataset.autoSaveInitialized === "true") {
                continue;
            }

            const fields = Array.from(form.querySelectorAll("[data-auto-save-field]"))
                .filter((field) => field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement);
            if (fields.length === 0) {
                continue;
            }

            form.dataset.autoSaveInitialized = "true";

            const statusElement = form.querySelector("[data-auto-save-status]");
            const idleStatusMessage = statusElement instanceof HTMLElement ? statusElement.textContent || "" : "";
            let resetStatusTimeoutId = null;
            let isSaving = false;
            let saveQueued = false;

            const requiresCsrf = form.hasAttribute("data-require-csrf");

            const updateDependentControls = () => {
                const registerButton = document.querySelector("[data-ssllabs-register-button]");
                if (!(registerButton instanceof HTMLButtonElement)) {
                    return;
                }

                const emailForm = document.querySelector("form[data-ssllabs-email-form]");
                const emailInput = emailForm?.querySelector("#ssllabs_email");
                const emailDirty = emailForm instanceof HTMLFormElement && App.serializeComparableFormState(emailForm) !== (emailForm.dataset.lastSavedSerializedState || emailForm.dataset.initialSerializedState || "");
                const emailBusy = emailForm instanceof HTMLFormElement && emailForm.dataset.autoSaveBusy === "true";
                const noEmailValue = emailInput instanceof HTMLInputElement && emailInput.value.trim() === "";

                registerButton.disabled = emailDirty || emailBusy || noEmailValue;
            };

            if (form.matches("form[data-ssllabs-email-form]")) {
                const emailInput = form.querySelector("#ssllabs_email");
                if (emailInput instanceof HTMLInputElement) {
                    const refreshDependentControls = () => updateDependentControls();
                    emailInput.addEventListener("input", refreshDependentControls);
                    emailInput.addEventListener("change", refreshDependentControls);
                    emailInput.addEventListener("blur", refreshDependentControls);
                }
            }

            const clearResetStatusTimeout = () => {
                if (resetStatusTimeoutId !== null) {
                    window.clearTimeout(resetStatusTimeoutId);
                    resetStatusTimeoutId = null;
                }
            };

            const scheduleStatusReset = () => {
                clearResetStatusTimeout();
                resetStatusTimeoutId = window.setTimeout(() => {
                    updateStatusElement(statusElement, idleStatusMessage, "muted");
                    resetStatusTimeoutId = null;
                }, 2500);
            };

            const snapshot = () => snapshotFields(fields);

            let lastSavedState = snapshot();
            form.dataset.lastSavedSerializedState = lastSavedState;
            updateDependentControls();

            const saveForm = async () => {
                const submittedState = snapshot();
                if (submittedState === lastSavedState) {
                    return;
                }
                if (isSaving) {
                    saveQueued = true;
                    return;
                }
                if (!form.reportValidity()) {
                    updateStatusElement(statusElement, "Fix validation errors before changes can be saved.", "danger");
                    return;
                }

                isSaving = true;
                saveQueued = false;
                form.dataset.autoSaveBusy = "true";
                clearResetStatusTimeout();
                updateStatusElement(statusElement, "Saving...", "muted");

                const csrfToken = typeof App.readCsrfToken === "function" ? App.readCsrfToken(form) : "";
                if (requiresCsrf && !csrfToken) {
                    const message = "Security token is missing. Reload the page and try again.";
                    updateStatusElement(statusElement, message, "danger");
                    App.pushInlineFlash?.("danger", message);
                    isSaving = false;
                    form.dataset.autoSaveBusy = "false";
                    updateDependentControls();
                    return;
                }

                try {
                    const response = await fetchWithTimeout(App.resolveSameOriginUrl(form.action), {
                        method: (form.method || "post").toUpperCase(),
                        body: new FormData(form),
                        credentials: "same-origin",
                        headers: {
                            Accept: "application/json",
                            "X-Requested-With": "XMLHttpRequest",
                            "X-CSRF-Token": csrfToken,
                        },
                    }, 10000);
                    const payload = await response.json().catch(() => ({}));
                    const message = typeof payload.message === "string" && payload.message
                        ? payload.message
                        : (response.ok ? "Saved." : "Save failed.");

                    if (!response.ok || payload.success === false) {
                        updateStatusElement(statusElement, message, "danger");
                        App.pushInlineFlash?.("danger", message);
                        return;
                    }

                    lastSavedState = submittedState;
                    form.dataset.lastSavedSerializedState = submittedState;

                    const caddyUrlField = fields.find((f) => f.name === "caddy_api_url");
                    if (caddyUrlField instanceof HTMLInputElement) {
                        const nonUrlFields = fields.filter((f) => f.name !== "caddy_api_url");
                        for (const field of nonUrlFields) {
                            field.dataset.serverValid = "true";
                            validateSettingsField(field);
                        }
                        void probeCaddySettingsValidity(caddyUrlField, []);
                    } else {
                        for (const field of fields) {
                            field.dataset.serverValid = "true";
                            validateSettingsField(field);
                        }
                    }

                    if (snapshot() === submittedState) {
                        updateStatusElement(statusElement, message, "success");
                        App.pushInlineFlash?.("success", message);
                        scheduleStatusReset();
                    } else {
                        updateStatusElement(statusElement, "Saving latest changes...", "muted");
                        saveQueued = true;
                    }
                } catch (error) {
                    const message = isAbortError(error)
                        ? "Automatic save timed out. Please try again."
                        : "Automatic save failed. Please try again.";
                    updateStatusElement(statusElement, message, "danger");
                    App.pushInlineFlash?.("danger", message);
                } finally {
                    isSaving = false;
                    form.dataset.autoSaveBusy = "false";
                    updateDependentControls();
                    const changedDuringSave = snapshot() !== submittedState;
                    if (saveQueued || changedDuringSave) {
                        void saveForm();
                    }
                }
            };

            form.addEventListener("submit", (event) => {
                event.preventDefault();
                void saveForm();
            });

            for (const field of fields) {
                validateSettingsField(field);

                if (field instanceof HTMLInputElement && field.type === "checkbox") {
                    field.addEventListener("change", () => {
                        validateSettingsField(field);
                        void saveForm();
                    });
                    continue;
                }

                field.addEventListener("input", () => {
                    delete field.dataset.serverValid;
                    validateSettingsField(field);
                });

                field.addEventListener("change", () => {
                    validateSettingsField(field);
                });

                field.addEventListener("blur", () => {
                    const isValid = validateSettingsField(field);
                    if (!isValid) {
                        field.reportValidity();
                        updateStatusElement(statusElement, "Fix validation errors before changes can be saved.", "danger");
                        return;
                    }
                    void saveForm();
                });
            }

            const initCaddyUrlField = fields.find((f) => f.name === "caddy_api_url");
            if (initCaddyUrlField instanceof HTMLInputElement) {
                const initOtherFields = fields.filter((f) => f.name !== "caddy_api_url");
                void probeCaddySettingsValidity(initCaddyUrlField, initOtherFields);
            }
        }
    };

    const domainTokenPattern = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{2,59})$/;

    const normalizeDomainToken = (value) => {
        const rawValue = String(value || "").trim().replace(/\.+$/u, "");
        if (!rawValue) {
            return null;
        }

        let parsedUrl;
        try {
            parsedUrl = new URL(`http://${rawValue}`);
        } catch {
            return null;
        }

        if (
            parsedUrl.username ||
            parsedUrl.password ||
            parsedUrl.port ||
            (parsedUrl.pathname && parsedUrl.pathname !== "/") ||
            parsedUrl.search ||
            parsedUrl.hash
        ) {
            return null;
        }

        const normalized = parsedUrl.hostname.toLowerCase().replace(/\.+$/u, "");

        if (normalized.length > 253 || !domainTokenPattern.test(normalized)) {
            return null;
        }
        return normalized;
    };

    const splitDomainTokenInput = (value) => String(value || "")
        .split(/[\s,]+/u)
        .map((token) => token.trim())
        .filter((token) => token.length > 0);

    App.initializeSiteDomainInputs = () => {
        for (const container of document.querySelectorAll("[data-domain-tag-input]")) {
            if (!(container instanceof HTMLElement) || container.dataset.domainTagInitialized === "true") {
                continue;
            }

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

            container.dataset.domainTagInitialized = "true";

            const selectedSiteId = Number.parseInt(container.dataset.selectedSiteId || "", 10);
            const maxDomainsValue = Number.parseInt(container.dataset.maxDomains || "25", 10);
            const maxDomains = Number.isInteger(maxDomainsValue) && maxDomainsValue > 0
                ? maxDomainsValue
                : 25;
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

            const originalPlaceholder = entryInput.placeholder;

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
                entryInput.placeholder = domains.length > 0 ? "" : originalPlaceholder;
            };

            const addDomains = (rawValue) => {
                const tokens = splitDomainTokenInput(rawValue);
                if (tokens.length === 0) {
                    return false;
                }

                const additions = [];
                for (const token of tokens) {
                    const normalized = normalizeDomainToken(token);
                    if (!normalized) {
                        setError(`'${token}' is not a valid domain.`);
                        return false;
                    }
                    if (blockedDomains.has(normalized)) {
                        setError(`'${normalized}' is already assigned to another site configuration.`);
                        return false;
                    }
                    if (domains.includes(normalized) || additions.includes(normalized)) {
                        continue;
                    }
                    additions.push(normalized);
                }

                if (domains.length + additions.length > maxDomains) {
                    setError(`A site can contain at most ${maxDomains} domains.`);
                    return false;
                }

                if (additions.length === 0) {
                    setError("");
                    return false;
                }

                domains = [...domains, ...additions];
                setError("");
                renderTags();
                updateHiddenValue();
                return true;
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
        const siteNameInput = form.elements.namedItem("site_name");
        const domainInput = form.elements.namedItem("domain");
        const directivesInput = form.elements.namedItem("caddy_directives");

        if (!(siteNameInput instanceof HTMLInputElement) || siteNameInput.value.trim() === "") {
            return false;
        }
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

    const serializeCaddyfileFormState = (form) => {
        const caddyfileInput = form.elements.namedItem("caddyfile");
        if (!(caddyfileInput instanceof HTMLTextAreaElement)) {
            return "";
        }
        return caddyfileInput.value;
    };

    const setButtonInteractionState = (button, enabled) => {
        if (!(button instanceof HTMLButtonElement)) {
            return;
        }
        button.disabled = !enabled;
        button.setAttribute("aria-disabled", enabled ? "false" : "true");
    };

    const readSiteConfigFields = (form) => {
        const siteNameInput = form.elements.namedItem("site_name");
        const domainInput = form.elements.namedItem("domain");
        const directivesInput = form.elements.namedItem("caddy_directives");
        const enabledInput = form.elements.namedItem("enabled");

        return {
            siteName: siteNameInput instanceof HTMLInputElement ? siteNameInput.value.trim() : "",
            domain: domainInput instanceof HTMLInputElement ? domainInput.value.trim() : "",
            caddyDirectives: directivesInput instanceof HTMLTextAreaElement ? directivesInput.value.trim() : "",
            enabled: enabledInput instanceof HTMLInputElement ? String(enabledInput.checked) : "false",
        };
    };

    const getInitialSiteConfigFields = (form) => {
        try {
            const parsed = JSON.parse(form.dataset.initialFieldState || "{}");
            return parsed && typeof parsed === "object" ? parsed : {};
        } catch {
            return {};
        }
    };

    const getSiteConfigChangeProfile = (form) => {
        const initialFields = getInitialSiteConfigFields(form);
        const currentFields = readSiteConfigFields(form);
        const changedKeys = [];

        for (const [key, currentValue] of Object.entries({
            site_name: currentFields.siteName,
            domain: currentFields.domain,
            caddy_directives: currentFields.caddyDirectives,
            enabled: currentFields.enabled,
        })) {
            if (String(initialFields[key] ?? "") !== currentValue) {
                changedKeys.push(key);
            }
        }

        const onlySiteNameChanged = changedKeys.length > 0 && changedKeys.every((key) => key === "site_name");
        const requiresDeploy = changedKeys.some((key) => key !== "site_name");
        return { changedKeys, onlySiteNameChanged, requiresDeploy };
    };

    const updateSiteSaveButtonMode = (form, saveButton, changeProfile) => {
        if (!(saveButton instanceof HTMLButtonElement)) {
            return;
        }

        const label = saveButton.querySelector("[data-loading-submit-label]");
        const safeMode = changeProfile.onlySiteNameChanged;
        saveButton.classList.toggle("js-confirm", !safeMode);
        saveButton.dataset.siteSaveMode = safeMode ? "safe" : "deploy";

        if (safeMode) {
            saveButton.dataset.loadingLabel = saveButton.dataset.loadingLabelSafe || "Saving...";
            saveButton.dataset.confirmTitle = saveButton.dataset.confirmTitleSafe || "Save";
            saveButton.dataset.confirm = saveButton.dataset.confirmSafe || "Save changes?";
            saveButton.dataset.confirmAccept = saveButton.dataset.confirmAcceptSafe || "Save";
            if (label instanceof HTMLElement) {
                label.textContent = "Save";
            }
            return;
        }

        saveButton.dataset.loadingLabel = saveButton.dataset.loadingLabelDeploy || "Saving & Deploying...";
        saveButton.dataset.confirmTitle = saveButton.dataset.confirmTitleDeploy || "Deploy changes";
        saveButton.dataset.confirm = saveButton.dataset.confirmDeploy || "Deploy changes?";
        saveButton.dataset.confirmAccept = saveButton.dataset.confirmAcceptDeploy || "Deploy changes";
        if (label instanceof HTMLElement) {
            label.textContent = "Save & Deploy";
        }
    };

    const updateSiteConfigFormActions = (form) => {
        if (!(form instanceof HTMLFormElement) || !form.hasAttribute("data-site-config-form")) {
            return;
        }

        const validateButton = form.querySelector("[data-validate-form-button]");
        const saveButton = form.querySelector("[data-site-save-button]");
        const statusElement = form.querySelector("[data-site-config-status]");
        const currentState = serializeSiteConfigFormState(form);
        const initialState = form.dataset.initialSerializedState || "";
        const lastValidatedState = form.dataset.lastValidatedState || "";
        const changeProfile = getSiteConfigChangeProfile(form);

        if (form.dataset.validationState === "valid" && currentState !== lastValidatedState) {
            form.dataset.validationState = "unvalidated";
        }

        const hasChanges = currentState !== initialState;
        const hasRequiredValues = siteConfigFormHasRequiredValues(form);
        const validationMatchesCurrentState = form.dataset.validationState === "valid" && currentState === lastValidatedState;
        const saveAllowed = hasChanges && hasRequiredValues && (changeProfile.onlySiteNameChanged || validationMatchesCurrentState);

        if (statusElement instanceof HTMLElement) {
            const validationState = form.dataset.validationState;
            let statusMessage = "";
            let statusClass = "text-body-secondary";

            if (validationState === "validating") {
                statusMessage = "Validating site configuration...";
            } else if (validationState === "invalid") {
                statusMessage = "Validation failed. Fix the highlighted fields.";
                statusClass = "text-danger";
            } else if (validationState === "valid") {
                statusMessage = saveAllowed
                    ? (changeProfile.requiresDeploy ? "Validated. Save & Deploy is ready." : "Validated. Save is ready.")
                    : "Validated.";
                statusClass = "text-success";
            } else if (!hasRequiredValues) {
                statusMessage = "Complete the required site fields.";
            } else if (hasChanges && changeProfile.requiresDeploy) {
                statusMessage = "Validate the current site configuration before saving.";
            } else if (hasChanges) {
                statusMessage = "Save is ready.";
                statusClass = "text-success";
            } else {
                statusMessage = "Make changes to enable validate and save.";
            }

            statusElement.textContent = statusMessage;
            statusElement.classList.remove("text-body-secondary", "text-success", "text-danger");
            statusElement.classList.add(statusClass);
        }

        setButtonInteractionState(validateButton, hasChanges && hasRequiredValues && changeProfile.requiresDeploy);
        setButtonInteractionState(saveButton, saveAllowed);
        updateSiteSaveButtonMode(form, saveButton, changeProfile);
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

        const currentState = serializeCaddyfileFormState(form);
        const initialState = form.dataset.initialSerializedState || "";
        const lastValidatedState = form.dataset.lastValidatedState || "";

        if (form.dataset.validationState === "valid" && currentState !== lastValidatedState) {
            form.dataset.validationState = "unvalidated";
        }

        const hasChanges = currentState !== initialState;
        const hasRequiredValues = caddyfileFormHasRequiredValues(form);
        const validationMatchesCurrentState = form.dataset.validationState === "valid" && currentState === lastValidatedState;

        setButtonInteractionState(validateButton, hasChanges && hasRequiredValues);
        setButtonInteractionState(saveButton, hasChanges && hasRequiredValues && validationMatchesCurrentState);
    };

    App.initializeSiteConfigForms = () => {
        for (const form of document.querySelectorAll("form[data-site-config-form]")) {
            if (!(form instanceof HTMLFormElement) || form.dataset.siteConfigInitialized === "true") {
                continue;
            }
            form.dataset.siteConfigInitialized = "true";
            const initialFields = readSiteConfigFields(form);
            form.dataset.initialSerializedState = serializeSiteConfigFormState(form);
            form.dataset.initialFieldState = JSON.stringify({
                site_name: initialFields.siteName,
                domain: initialFields.domain,
                caddy_directives: initialFields.caddyDirectives,
                enabled: initialFields.enabled,
            });
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
                const changeProfile = getSiteConfigChangeProfile(form);
                if (changeProfile.requiresDeploy) {
                    App.pushInlineFlash("warning", "Validate the current site configuration before saving.");
                    return;
                }
                App.pushInlineFlash("warning", "Complete the required site fields before saving.");
            });

            syncFormState();
        }
    };

    App.initializeSitesSearch = () => {
        const input = document.querySelector("[data-sites-search-input]");
        const tbody = document.querySelector(".sites-table tbody");
        const countEl = document.querySelector("[data-sites-count]");
        if (!(input instanceof HTMLInputElement) || !(tbody instanceof HTMLElement)) {
            return;
        }

        const getDataRows = () => Array.from(tbody.querySelectorAll("tr:not(.sites-search-empty)"))
            .filter((row) => !row.querySelector("td[colspan]"));

        const applyFilter = () => {
            const q = input.value.trim().toLowerCase();
            const rows = getDataRows();
            let visibleCount = 0;

            for (const row of rows) {
                const name = row.querySelector(".fw-semibold")?.textContent?.toLowerCase() ?? "";
                const domains = Array.from(row.querySelectorAll(".site-domain-badge"))
                    .map((el) => el.textContent.trim().toLowerCase());
                const matches = !q || name.includes(q) || domains.some((d) => d.includes(q));
                if (matches) {
                    delete row.dataset.searchHidden;
                    visibleCount++;
                } else {
                    row.dataset.searchHidden = "";
                }
            }

            let emptyRow = tbody.querySelector(".sites-search-empty");
            if (q && visibleCount === 0) {
                if (!emptyRow) {
                    emptyRow = document.createElement("tr");
                    emptyRow.className = "sites-search-empty";
                    const td = document.createElement("td");
                    td.colSpan = 4;
                    td.className = "text-center text-body-secondary py-4";
                    td.textContent = "No sites match your search.";
                    emptyRow.append(td);
                    tbody.append(emptyRow);
                }
            } else {
                emptyRow?.remove();
            }

            if (countEl instanceof HTMLElement) {
                const total = rows.length;
                countEl.textContent = q
                    ? `${visibleCount} of ${total}`
                    : `${total} configured`;
            }
        };

        let debounceTimer = null;
        input.addEventListener("input", () => {
            clearTimeout(debounceTimer);
            debounceTimer = window.setTimeout(applyFilter, 180);
        });
    };

    // On small screens the create/edit site form is relocated into a Bootstrap
    // modal that is opened automatically when an existing site is being edited.
    // On larger screens it stays inline.
    App.initializeSiteFormModal = () => {
        const section = document.querySelector(".app-page--sites");
        const modalElement = document.getElementById("site-form-modal");
        const formPanel = document.getElementById("form-panel");
        const desktopHome = document.querySelector("[data-site-form-home]");
        const modalBody = modalElement?.querySelector("[data-site-form-modal-body]");

        if (!(section instanceof HTMLElement) || !(modalElement instanceof HTMLElement)
            || !(formPanel instanceof HTMLElement) || !(desktopHome instanceof HTMLElement)
            || !(modalBody instanceof HTMLElement)) {
            return;
        }

        const mobileQuery = window.matchMedia("(max-width: 767.98px)");
        const getModal = () => (window.bootstrap?.Modal
            ? window.bootstrap.Modal.getOrCreateInstance(modalElement)
            : null);

        const placeForViewport = () => {
            if (mobileQuery.matches) {
                if (formPanel.parentElement !== modalBody) {
                    modalBody.append(formPanel);
                }
            } else {
                getModal()?.hide();
                if (formPanel.parentElement !== desktopHome) {
                    desktopHome.append(formPanel);
                }
            }
        };

        if (typeof section.siteFormModalCleanup === "function") {
            section.siteFormModalCleanup();
        }
        if (!App.markInitialized(section, "siteFormModalInitialized")) {
            return;
        }

        section.classList.add("sites-modal-enabled");

        placeForViewport();
        const handleOpenClick = (event) => {
            if (!(event.target instanceof Element)) {
                return;
            }
            const trigger = event.target.closest("[data-site-form-modal-open]");
            if (!(trigger instanceof HTMLElement) || !mobileQuery.matches) {
                return;
            }
            event.preventDefault();
            getModal()?.show();
        };

        const cleanupSiteFormModal = () => {
            mobileQuery.removeEventListener("change", placeForViewport);
            document.removeEventListener("click", handleOpenClick);
            window.removeEventListener("beforeunload", cleanupSiteFormModal);
            delete section.dataset.siteFormModalInitialized;
            if (section.siteFormModalCleanup === cleanupSiteFormModal) {
                section.siteFormModalCleanup = null;
            }
        };

        section.siteFormModalCleanup = cleanupSiteFormModal;
        mobileQuery.addEventListener("change", placeForViewport);

        document.addEventListener("click", handleOpenClick);
        window.addEventListener("beforeunload", cleanupSiteFormModal, { once: true });

        // Editing an existing site reloads the page with the form pre-filled;
        // surface it straight away on mobile so the edit isn't hidden.
        if (section.hasAttribute("data-site-form-open") && mobileQuery.matches) {
            getModal()?.show();
        }
    };

    App.initializeCaddyfileForms = () => {
        for (const form of document.querySelectorAll("form[data-caddyfile-config-form]")) {
            if (!(form instanceof HTMLFormElement) || form.dataset.caddyfileConfigInitialized === "true") {
                continue;
            }
            form.dataset.caddyfileConfigInitialized = "true";
            form.dataset.initialSerializedState = serializeCaddyfileFormState(form);
            form.dataset.lastValidatedState = "";
            form.dataset.validationState = "unvalidated";

            const syncFormState = () => {
                updateCaddyfileFormActions(form);
            };

            form.addEventListener("input", syncFormState);
            form.addEventListener("change", syncFormState);
            form.addEventListener("submit", (event) => {
                const submitter = event.submitter;
                if (!(submitter instanceof HTMLElement)) {
                    return;
                }

                const saveButton = submitter.matches("#caddyfile-save-btn")
                    ? submitter
                    : submitter.closest("#caddyfile-save-btn");
                if (!(saveButton instanceof HTMLButtonElement)) {
                    return;
                }

                updateCaddyfileFormActions(form);
                if (!saveButton.disabled) {
                    return;
                }

                event.preventDefault();
                App.pushInlineFlash("warning", "Validate the Caddyfile configuration before saving.");
            });

            syncFormState();
        }
    };

    App.initializeValidateButtons = () => {
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
                    requestUrl = App.resolveSameOriginUrl(validateUrl);
                } catch {
                    App.pushInlineFlash("danger", "Invalid validation URL.");
                    return;
                }

                const spinner = button.querySelector("[data-validate-button-spinner]");
                const label = button.querySelector("[data-validate-button-label]");
                const originalLabel = label instanceof HTMLElement ? label.textContent : button.textContent;
                const serializeFormState = () => (form.hasAttribute("data-caddyfile-config-form")
                    ? serializeCaddyfileFormState(form)
                    : serializeSiteConfigFormState(form));
                const submittedState = serializeFormState();
                const headers = new Headers({ "X-Requested-With": "fetch" });
                const csrfToken = typeof App.readCsrfToken === "function" ? App.readCsrfToken(form) : "";
                if (!csrfToken) {
                    App.pushInlineFlash("danger", "Security token is missing. Reload the page and try again.");
                    return;
                }
                headers.set("X-CSRF-Token", csrfToken);

                form.dataset.validationState = "validating";
                updateSiteConfigFormActions(form);
                updateCaddyfileFormActions(form);

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
                    const response = await fetchWithTimeout(requestUrl, {
                        method: "POST",
                        body: new FormData(form),
                        credentials: "same-origin",
                        headers,
                    }, 10000);
                    const contentType = response.headers.get("content-type") || "";
                    let payload = {};

                    if (contentType.includes("application/json")) {
                        payload = await response.json();
                    } else {
                        payload = { message: (await response.text()).trim() };
                    }

                    const successPrefix = button.dataset.validateSuccessPrefix || "Validation successful";
                    const errorPrefix = button.dataset.validateErrorPrefix || "Validation failed";
                    const successMessage = typeof payload.message === "string" && payload.message
                        ? payload.message
                        : "Configuration is valid.";

                    if (serializeFormState() !== submittedState) {
                        form.dataset.validationState = "unvalidated";
                        form.dataset.lastValidatedState = "";
                        App.pushInlineFlash("warning", "Form changed during validation. Validate again before saving.");
                        return;
                    }

                    if (response.ok && payload.valid) {
                        // Apply formatted directives if returned (Sites page)
                        if (typeof payload.formatted_caddy_directives === "string") {
                            const directivesField = form.querySelector("#site-caddy-directives");
                            if (directivesField instanceof HTMLTextAreaElement) {
                                directivesField.value = payload.formatted_caddy_directives;
                                directivesField.dispatchEvent(new Event("input", { bubbles: true }));
                            }
                        }
                        // Apply formatted caddyfile if returned (Caddyfile page)
                        if (typeof payload.formatted_caddyfile === "string") {
                            const caddyfileField = form.querySelector("#global-caddyfile");
                            if (caddyfileField instanceof HTMLTextAreaElement) {
                                caddyfileField.value = payload.formatted_caddyfile;
                                caddyfileField.dispatchEvent(new Event("input", { bubbles: true }));
                            }
                        }
                        // Capture state AFTER formatting is applied
                        form.dataset.lastValidatedState = serializeFormState();
                        form.dataset.validationState = "valid";
                        App.pushInlineFlash("success", `${successPrefix}: ${successMessage}`);
                    } else {
                        form.dataset.validationState = "invalid";
                        const message = typeof payload.message === "string" && payload.message
                            ? payload.message
                            : "Unknown validation error.";
                        App.pushInlineFlash("danger", `${errorPrefix}: ${message}`);
                    }
                } catch (error) {
                    form.dataset.validationState = "invalid";
                    App.pushInlineFlash(
                        "danger",
                        isAbortError(error)
                            ? "Validation request timed out. Please try again."
                            : "Validation request failed. Please try again."
                    );
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

    App.initializeAutoSubmitForms = () => {
        for (const form of document.querySelectorAll("form[data-auto-submit-form]")) {
            if (!(form instanceof HTMLFormElement) || form.dataset.autoSubmitInitialized === "true") {
                continue;
            }
            form.dataset.autoSubmitInitialized = "true";

            for (const field of form.querySelectorAll("[data-auto-submit-field]")) {
                if (!(field instanceof HTMLInputElement || field instanceof HTMLSelectElement || field instanceof HTMLTextAreaElement)) {
                    continue;
                }

                field.addEventListener("change", () => {
                    if (field.disabled || form.dataset.autoSubmitting === "true") {
                        return;
                    }
                    form.dataset.autoSubmitting = "true";
                    form.requestSubmit();
                });
            }

            form.addEventListener("submit", () => {
                form.dataset.autoSubmitting = "false";
            });
        }
    };

    App.initializeSettingsPasswordValidation = () => {
        const passwordInput = document.getElementById("new_password");
        const confirmInput = document.getElementById("confirm_password");
        if (!(passwordInput instanceof HTMLInputElement) || !(confirmInput instanceof HTMLInputElement)) {
            return;
        }
        if (passwordInput.dataset.passwordValidationInitialized === "true") {
            return;
        }
        passwordInput.dataset.passwordValidationInitialized = "true";

        const validatePasswordStrength = () => {
            validateSettingsField(passwordInput);
        };

        const validatePasswordMatch = () => {
            confirmInput.setCustomValidity(
                confirmInput.value && passwordInput.value !== confirmInput.value
                    ? "New passwords do not match."
                    : ""
            );
            setFieldFeedbackState(confirmInput);
        };

        passwordInput.addEventListener("input", () => {
            validatePasswordStrength();
            validatePasswordMatch();
        });
        passwordInput.addEventListener("blur", () => {
            validatePasswordStrength();
            passwordInput.reportValidity();
        });
        confirmInput.addEventListener("input", validatePasswordMatch);
        confirmInput.addEventListener("blur", () => {
            validatePasswordMatch();
            confirmInput.reportValidity();
        });
    };
})();
//
// app/static/js/app-forms.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

(() => {
    "use strict";

    const App = window.CaddyBuddyApp || (window.CaddyBuddyApp = {});
    const settingsEmailPattern = /^[^@\s]+@[^@\s]+\.[^@\s]+$/u;
    const minimumPasswordLength = 8;
    const passwordPolicyMessage = `Password must be at least ${minimumPasswordLength} characters long and contain uppercase, lowercase, digit, and special character.`;

    const setFieldFeedbackState = (field) => {
        if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement)) {
            return;
        }

        const hasValue = field instanceof HTMLInputElement && field.type === "checkbox"
            ? true
            : field.value.trim() !== "";
        const isValid = field.checkValidity();

        field.classList.toggle("is-invalid", !isValid);
        field.classList.toggle("is-valid", hasValue && isValid);
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
                    } else if (parsed.port) {
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
        } else if (field.name === "new_password") {
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
            form.dataset.autoSaveInitialized = "true";

            const fields = Array.from(form.querySelectorAll("[data-auto-save-field]"))
                .filter((field) => field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement);
            if (fields.length === 0) {
                continue;
            }

            const statusElement = form.querySelector("[data-auto-save-status]");
            const idleStatusMessage = statusElement instanceof HTMLElement ? statusElement.textContent || "" : "";
            let resetStatusTimeoutId = null;
            let isSaving = false;
            let saveQueued = false;

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

            const snapshot = () => fields
                .map((field) => `${field.name}:${getFieldValue(field)}`)
                .join("\u001f");

            let lastSavedState = snapshot();

            const saveForm = async () => {
                const currentState = snapshot();
                if (currentState === lastSavedState) {
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
                clearResetStatusTimeout();
                updateStatusElement(statusElement, "Saving...", "muted");

                try {
                    const response = await fetch(App.resolveSameOriginUrl(form.action), {
                        method: (form.method || "post").toUpperCase(),
                        body: new FormData(form),
                        credentials: "same-origin",
                        headers: {
                            Accept: "application/json",
                            "X-Requested-With": "XMLHttpRequest",
                        },
                    });
                    const payload = await response.json().catch(() => ({}));
                    const message = typeof payload.message === "string" && payload.message
                        ? payload.message
                        : (response.ok ? "Saved." : "Save failed.");

                    if (!response.ok || payload.success === false) {
                        updateStatusElement(statusElement, message, "danger");
                        App.pushInlineFlash?.("danger", message);
                        return;
                    }

                    lastSavedState = snapshot();
                    updateStatusElement(statusElement, message, "success");
                    App.pushInlineFlash?.("success", message);
                    scheduleStatusReset();
                } catch {
                    const message = "Automatic save failed. Please try again.";
                    updateStatusElement(statusElement, message, "danger");
                    App.pushInlineFlash?.("danger", message);
                } finally {
                    isSaving = false;
                    if (saveQueued || snapshot() !== lastSavedState) {
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
        }
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

    App.initializeSiteDomainInputs = () => {
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
                const submittedState = form.hasAttribute("data-caddyfile-config-form")
                    ? serializeCaddyfileFormState(form)
                    : serializeSiteConfigFormState(form);
                const headers = new Headers({ "X-Requested-With": "fetch" });
                const csrfToken = App.readCsrfToken(form);
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
                    const successMessage = typeof payload.message === "string" && payload.message
                        ? payload.message
                        : "Configuration is valid.";

                    if (response.ok && payload.valid) {
                        form.dataset.lastValidatedState = submittedState;
                        form.dataset.validationState = "valid";
                        App.pushInlineFlash("success", `${successPrefix}: ${successMessage}`);
                    } else {
                        form.dataset.validationState = "invalid";
                        const message = typeof payload.message === "string" && payload.message
                            ? payload.message
                            : "Unknown validation error.";
                        App.pushInlineFlash("danger", `${errorPrefix}: ${message}`);
                    }
                } catch {
                    form.dataset.validationState = "invalid";
                    App.pushInlineFlash("danger", "Validation request failed. Please try again.");
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
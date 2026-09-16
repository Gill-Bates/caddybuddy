//
// app/static/js/passkeys.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

// WebAuthn ceremonies for passkey sign-in (login page) and passkey enrollment
// (Settings -> Security). The server renders the passkey list, so this module
// only drives navigator.credentials and reloads the page afterwards.

(() => {
    "use strict";

    const App = window.CaddyBuddyApp || (window.CaddyBuddyApp = {});
    const supported = typeof window.PublicKeyCredential === "function"
        && typeof navigator.credentials?.create === "function"
        && typeof navigator.credentials?.get === "function";

    const base64UrlToBytes = (value) => {
        const base64 = String(value).replace(/-/g, "+").replace(/_/g, "/");
        const binary = window.atob(base64 + "=".repeat((4 - base64.length % 4) % 4));
        const bytes = new Uint8Array(binary.length);
        for (let index = 0; index < binary.length; index += 1) {
            bytes[index] = binary.charCodeAt(index);
        }
        return bytes;
    };

    const bytesToBase64Url = (buffer) => {
        const bytes = new Uint8Array(buffer);
        let binary = "";
        for (const byte of bytes) {
            binary += String.fromCharCode(byte);
        }
        return window.btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    };

    const resolveUrl = (rawUrl) => (
        typeof App.resolveSameOriginUrl === "function"
            ? App.resolveSameOriginUrl(rawUrl)
            : new URL(rawUrl, window.location.origin).toString()
    );

    const requestJson = async (url, body) => {
        const headers = {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        };
        const csrfToken = typeof App.readCsrfToken === "function" ? App.readCsrfToken() : "";
        if (csrfToken) {
            headers["X-CSRF-Token"] = csrfToken;
        }

        const response = await fetch(resolveUrl(url), {
            method: "POST",
            credentials: "same-origin",
            headers,
            body: JSON.stringify(body ?? {}),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = typeof payload.detail === "string" && payload.detail
                ? payload.detail
                : `Request failed with status ${response.status}.`;
            throw new Error(detail);
        }
        return payload;
    };

    // The server returns standard WebAuthn JSON, so base64url fields have to be
    // decoded into the ArrayBuffers navigator.credentials expects.
    const toCreationOptions = (options) => {
        const publicKey = {
            ...options,
            challenge: base64UrlToBytes(options.challenge),
            user: { ...options.user, id: base64UrlToBytes(options.user.id) },
        };
        if (Array.isArray(options.excludeCredentials)) {
            publicKey.excludeCredentials = options.excludeCredentials.map((credential) => ({
                ...credential,
                id: base64UrlToBytes(credential.id),
            }));
        }
        return publicKey;
    };

    const toRequestOptions = (options) => {
        const publicKey = { ...options, challenge: base64UrlToBytes(options.challenge) };
        if (Array.isArray(options.allowCredentials)) {
            publicKey.allowCredentials = options.allowCredentials.map((credential) => ({
                ...credential,
                id: base64UrlToBytes(credential.id),
            }));
        }
        return publicKey;
    };

    const credentialToJson = (credential) => {
        const response = { clientDataJSON: bytesToBase64Url(credential.response.clientDataJSON) };

        if (credential.response.attestationObject) {
            response.attestationObject = bytesToBase64Url(credential.response.attestationObject);
            if (typeof credential.response.getTransports === "function") {
                response.transports = credential.response.getTransports();
            }
        }
        if (credential.response.authenticatorData && credential.response.signature) {
            response.authenticatorData = bytesToBase64Url(credential.response.authenticatorData);
            response.signature = bytesToBase64Url(credential.response.signature);
            response.userHandle = credential.response.userHandle
                ? bytesToBase64Url(credential.response.userHandle)
                : null;
        }

        return {
            id: credential.id,
            rawId: bytesToBase64Url(credential.rawId),
            type: credential.type,
            clientExtensionResults: {},
            response,
        };
    };

    const setBusy = (button, busy, busyLabel) => {
        if (!(button instanceof HTMLButtonElement)) {
            return;
        }
        const spinner = button.querySelector("[data-passkey-spinner]");
        const label = button.querySelector("[data-passkey-label]");
        if (busy) {
            button.dataset.idleLabel = label?.textContent ?? "";
        }
        button.disabled = busy;
        button.setAttribute("aria-busy", busy ? "true" : "false");
        spinner?.classList.toggle("d-none", !busy);
        if (label instanceof HTMLElement) {
            label.textContent = busy ? busyLabel : (button.dataset.idleLabel || label.textContent);
        }
    };

    // A cancelled or timed-out ceremony is a normal outcome, not an error.
    const isUserAbort = (error) => error instanceof DOMException
        && (error.name === "NotAllowedError" || error.name === "AbortError");

    const initializeLogin = () => {
        const root = document.querySelector("[data-passkey-login]");
        if (!(root instanceof HTMLElement)) {
            return;
        }
        if (typeof App.markInitialized === "function"
            && !App.markInitialized(root, "passkeyLoginInitialized")) {
            return;
        }

        const button = root.querySelector("[data-passkey-login-button]");
        const startUrl = root.dataset.passkeyStartUrl;
        const finishUrl = root.dataset.passkeyFinishUrl;
        if (!(button instanceof HTMLButtonElement) || !startUrl || !finishUrl) {
            return;
        }
        if (!supported) {
            root.querySelector("[data-passkey-unsupported]")?.classList.remove("d-none");
            button.disabled = true;
            return;
        }

        let conditionalAbort = null;

        const signIn = async (mediation) => {
            const { options } = await requestJson(startUrl, {});
            const credentialRequest = { publicKey: toRequestOptions(options) };
            if (mediation) {
                conditionalAbort = new AbortController();
                credentialRequest.mediation = mediation;
                credentialRequest.signal = conditionalAbort.signal;
            }

            const credential = await navigator.credentials.get(credentialRequest);
            if (!credential) {
                throw new Error("The passkey request returned no credential.");
            }

            const result = await requestJson(finishUrl, {
                credential: credentialToJson(credential),
                next_url: root.dataset.passkeyNextUrl || "/",
            });
            window.location.assign(resolveUrl(result.redirect_url || "/"));
        };

        button.addEventListener("click", async () => {
            conditionalAbort?.abort();
            conditionalAbort = null;
            setBusy(button, true, "Verifying passkey...");
            try {
                await signIn(null);
            } catch (error) {
                if (!isUserAbort(error)) {
                    App.pushInlineFlash?.(
                        "danger",
                        error instanceof Error && error.message
                            ? error.message
                            : "Passkey sign-in failed.",
                    );
                }
                setBusy(button, false, "");
            }
        });

        // Conditional mediation lets the browser offer the passkey straight from
        // the username field, with no extra click.
        const startConditional = async () => {
            if (typeof window.PublicKeyCredential.isConditionalMediationAvailable !== "function") {
                return;
            }
            if (!await window.PublicKeyCredential.isConditionalMediationAvailable()) {
                return;
            }
            document.getElementById("username")?.setAttribute("autocomplete", "username webauthn");
            await signIn("conditional");
        };

        startConditional().catch((error) => {
            if (!isUserAbort(error)) {
                console.debug("Conditional passkey sign-in unavailable:", error);
            }
        });

        window.addEventListener("pagehide", () => {
            conditionalAbort?.abort();
            conditionalAbort = null;
        });
    };

    // A passkey registration reloads the page (the list is server-rendered),
    // so a flash shown before the reload would never be seen. Stash it across
    // the reload instead and surface it once the new page has loaded.
    const PASSKEY_FLASH_STORAGE_KEY = "caddybuddy:passkeyRegisteredFlash";

    const showPendingRegistrationFlash = () => {
        let message = null;
        try {
            message = sessionStorage.getItem(PASSKEY_FLASH_STORAGE_KEY);
            if (message) {
                sessionStorage.removeItem(PASSKEY_FLASH_STORAGE_KEY);
            }
        } catch {
            // Storage may be unavailable (e.g. private browsing); nothing to show then.
        }
        if (message) {
            App.pushInlineFlash?.("success", message);
        }
    };

    const initializeEnrollment = () => {
        const root = document.querySelector("[data-passkey-settings]");
        if (!(root instanceof HTMLElement)) {
            return;
        }
        showPendingRegistrationFlash();
        if (typeof App.markInitialized === "function"
            && !App.markInitialized(root, "passkeySettingsInitialized")) {
            return;
        }

        const button = root.querySelector("[data-passkey-register-button]");
        const nameInput = root.querySelector("[data-passkey-device-name]");
        const passwordInput = root.querySelector("[data-passkey-current-password]");
        const startUrl = root.dataset.passkeyStartUrl;
        const finishUrl = root.dataset.passkeyFinishUrl;
        if (!(button instanceof HTMLButtonElement) || !startUrl || !finishUrl) {
            return;
        }
        if (!supported) {
            root.querySelector("[data-passkey-unsupported]")?.classList.remove("d-none");
            button.disabled = true;
            return;
        }

        button.addEventListener("click", async () => {
            if (passwordInput instanceof HTMLInputElement && !passwordInput.reportValidity()) {
                return;
            }
            setBusy(button, true, "Waiting for authenticator...");
            try {
                const { options } = await requestJson(startUrl, {
                    current_password: passwordInput instanceof HTMLInputElement ? passwordInput.value : "",
                });
                const credential = await navigator.credentials.create({
                    publicKey: toCreationOptions(options),
                });
                if (!credential) {
                    throw new Error("The authenticator returned no credential.");
                }
                const finishResponse = await requestJson(finishUrl, {
                    credential: credentialToJson(credential),
                    device_name: nameInput instanceof HTMLInputElement ? nameInput.value : null,
                });
                try {
                    sessionStorage.setItem(
                        PASSKEY_FLASH_STORAGE_KEY,
                        finishResponse?.message || "Passkey added.",
                    );
                } catch {
                    // Storage may be unavailable (e.g. private browsing); the reload still succeeds.
                }
                // The passkey list is server-rendered; reload to show it, keeping
                // the user on the Passkey tab instead of falling back to General.
                window.location.assign(`${resolveUrl(root.dataset.passkeyReloadUrl || "/settings")}#settingsPasskeyPanel`);
            } catch (error) {
                if (!isUserAbort(error)) {
                    App.pushInlineFlash?.(
                        "danger",
                        error instanceof Error && error.message
                            ? error.message
                            : "The passkey could not be added.",
                    );
                }
                setBusy(button, false, "");
            }
        });
    };

    const initialize = () => {
        initializeLogin();
        initializeEnrollment();
    };

    App.initializePasskeys = initialize;

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initialize, { once: true });
    } else {
        initialize();
    }
})();

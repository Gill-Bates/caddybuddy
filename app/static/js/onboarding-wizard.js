//
// app/static/js/onboarding-wizard.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

(() => {
    "use strict";

    const App = window.CaddyBuddyApp || (window.CaddyBuddyApp = {});
    const SWIPE_THRESHOLD_PX = 56;
    const FIELD_CHECK_LABELS = {
        idle: "Not checked",
        checking: "Checking...",
        passed: "Ready",
        failed: "Needs review",
        stale: "Changed since check",
    };

    const clampStep = (value, min, max) => Math.min(Math.max(value, min), max);
    const isEditableTarget = (target) => (
        target instanceof HTMLInputElement
        || target instanceof HTMLTextAreaElement
        || target instanceof HTMLSelectElement
        || (target instanceof HTMLElement && target.isContentEditable)
    );
    const readFieldValue = (field) => {
        if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement) {
            return field.value.trim();
        }
        return "";
    };

    App.initializeOnboardingWizard = () => {
        for (const wizard of document.querySelectorAll("[data-onboarding-wizard]")) {
            if (!(wizard instanceof HTMLElement)) {
                continue;
            }

            const viewport = wizard.querySelector("[data-wizard-viewport]");
            const track = wizard.querySelector("[data-wizard-track]");
            const stepButtons = [...wizard.querySelectorAll("[data-wizard-step-button]")];
            const stepPanels = [...wizard.querySelectorAll("[data-wizard-step]")];

            if (!(viewport instanceof HTMLElement) || !(track instanceof HTMLElement)) {
                continue;
            }

            if (!App.markInitialized(wizard, "onboardingWizardInitialized")) {
                continue;
            }

            const stepTargets = stepButtons
                .map((button) => Number.parseInt(button.dataset.stepTarget || "", 10))
                .filter(Number.isFinite);
            const detectedMaxStep = Math.max(1, ...stepTargets);
            const configuredMaxStep = Number.parseInt(wizard.dataset.maxStep || "", 10);
            const maxStep = Number.isFinite(configuredMaxStep)
                ? clampStep(configuredMaxStep, 1, detectedMaxStep)
                : detectedMaxStep;
            let currentStep = clampStep(Number.parseInt(wizard.dataset.currentStep || "1", 10) || 1, 1, maxStep);
            let startX = 0;
            let startY = 0;
            let trackingTouch = false;
            let activePointerId = null;

            wizard.classList.add("is-enhanced");

            const preflightForm = wizard.querySelector("form[data-onboarding-preflight-form]");
            if (preflightForm instanceof HTMLFormElement) {
                const fieldChecks = new Map();

                const setFieldCheckStatus = (fieldCheck, status) => {
                    if (!(fieldCheck instanceof HTMLElement)) {
                        return;
                    }
                    fieldCheck.dataset.fieldCheckStatus = status;
                    const label = fieldCheck.querySelector(".cb-field-check__label");
                    if (label instanceof HTMLElement) {
                        label.textContent = FIELD_CHECK_LABELS[status] || FIELD_CHECK_LABELS.idle;
                    }
                };

                for (const fieldCheck of preflightForm.querySelectorAll("[data-field-check]")) {
                    if (!(fieldCheck instanceof HTMLElement)) {
                        continue;
                    }
                    const fieldName = fieldCheck.dataset.fieldCheckName || "";
                    if (!fieldName) {
                        continue;
                    }
                    fieldCheck.dataset.fieldCheckPersistedStatus = fieldCheck.dataset.fieldCheckStatus || "idle";
                    fieldChecks.set(fieldName, fieldCheck);
                }

                for (const field of preflightForm.querySelectorAll("[data-field-check-input]")) {
                    if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement)) {
                        continue;
                    }
                    const fieldName = field.dataset.fieldCheckInput || "";
                    const fieldCheck = fieldChecks.get(fieldName);
                    if (!(fieldCheck instanceof HTMLElement)) {
                        continue;
                    }

                    const syncFieldCheckState = () => {
                        const persistedStatus = fieldCheck.dataset.fieldCheckPersistedStatus || "idle";
                        const checkedValue = fieldCheck.dataset.fieldCheckValue || "";
                        const currentValue = readFieldValue(field);

                        if (currentValue === checkedValue) {
                            setFieldCheckStatus(fieldCheck, persistedStatus);
                            return;
                        }
                        if (persistedStatus === "passed" || persistedStatus === "failed") {
                            setFieldCheckStatus(fieldCheck, "stale");
                            return;
                        }
                        setFieldCheckStatus(fieldCheck, "idle");
                    };

                    field.addEventListener("input", syncFieldCheckState);
                    field.addEventListener("change", syncFieldCheckState);
                }

                preflightForm.addEventListener("submit", (event) => {
                    if (event.defaultPrevented) {
                        return;
                    }
                    for (const fieldCheck of fieldChecks.values()) {
                        setFieldCheckStatus(fieldCheck, "checking");
                    }
                });
            }

            const updateStepUi = () => {
                wizard.dataset.currentStep = String(currentStep);

                for (const button of stepButtons) {
                    if (!(button instanceof HTMLButtonElement)) {
                        continue;
                    }

                    const stepTarget = Number.parseInt(button.dataset.stepTarget || "", 10);
                    const step = Number.isFinite(stepTarget) ? stepTarget : 1;
                    const isActive = step === currentStep;
                    const isUnlocked = step >= 1 && step <= maxStep;
                    button.classList.toggle("is-active", isActive);
                    button.classList.toggle("is-inactive", !isActive);
                    if (isActive) {
                        button.setAttribute("aria-current", "step");
                    } else {
                        button.removeAttribute("aria-current");
                    }
                    button.disabled = !isUnlocked;
                    button.setAttribute("aria-disabled", isUnlocked ? "false" : "true");
                }

                for (const step of stepPanels) {
                    if (!(step instanceof HTMLElement)) {
                        continue;
                    }
                    const isActive = step.dataset.step === String(currentStep);
                    step.hidden = !isActive;
                    step.toggleAttribute("inert", !isActive);
                }
            };

            const focusCurrentStep = () => {
                const step = wizard.querySelector(`[data-wizard-step][data-step="${currentStep}"]`);
                if (!(step instanceof HTMLElement)) {
                    return;
                }
                if (!step.hasAttribute("tabindex")) {
                    step.setAttribute("tabindex", "-1");
                }
                step.focus({ preventScroll: true });
            };

            const moveToStep = (targetStep) => {
                const next = clampStep(targetStep, 1, maxStep);
                if (next === currentStep) {
                    return;
                }
                currentStep = next;
                updateStepUi();
                focusCurrentStep();
            };

            for (const button of stepButtons) {
                if (!(button instanceof HTMLButtonElement)) {
                    continue;
                }
                button.addEventListener("click", () => {
                    moveToStep(Number.parseInt(button.dataset.stepTarget || "1", 10) || 1);
                });
            }

            const releaseActivePointerCapture = () => {
                if (activePointerId === null || !viewport.hasPointerCapture?.(activePointerId)) {
                    return;
                }
                viewport.releasePointerCapture(activePointerId);
            };

            viewport.addEventListener("pointerdown", (event) => {
                if (event.pointerType === "mouse") {
                    return;
                }
                activePointerId = event.pointerId;
                trackingTouch = true;
                startX = event.clientX;
                startY = event.clientY;
                viewport.setPointerCapture(event.pointerId);
            });

            viewport.addEventListener("pointerup", (event) => {
                if (!trackingTouch || event.pointerId !== activePointerId) {
                    return;
                }

                const deltaX = event.clientX - startX;
                const deltaY = event.clientY - startY;
                releaseActivePointerCapture();
                trackingTouch = false;
                activePointerId = null;

                if (Math.abs(deltaX) < SWIPE_THRESHOLD_PX || Math.abs(deltaX) <= Math.abs(deltaY)) {
                    return;
                }

                moveToStep(currentStep + (deltaX < 0 ? 1 : -1));
            });

            const cancelPointerTracking = () => {
                releaseActivePointerCapture();
                trackingTouch = false;
                activePointerId = null;
            };

            viewport.addEventListener("pointercancel", cancelPointerTracking);
            viewport.addEventListener("lostpointercapture", cancelPointerTracking);

            wizard.addEventListener("keydown", (event) => {
                if (isEditableTarget(event.target)) {
                    return;
                }

                if (event.key === "ArrowLeft") {
                    event.preventDefault();
                    moveToStep(currentStep - 1);
                } else if (event.key === "ArrowRight") {
                    event.preventDefault();
                    moveToStep(currentStep + 1);
                }
            });

            updateStepUi();
        }
    };

    document.addEventListener("DOMContentLoaded", () => {
        App.initializeOnboardingWizard();
    });
})();

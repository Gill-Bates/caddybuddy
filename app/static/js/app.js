//
// app/static/js/app.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

"use strict";

const clearJsonEditorErrorState = (editor) => {
    editor.classList.remove("is-invalid");
    editor.removeAttribute("aria-invalid");
};

const markJsonEditorInvalid = (editor) => {
    editor.classList.add("is-invalid");
    editor.setAttribute("aria-invalid", "true");
};

const reformatJsonEditor = (editor) => {
    const value = editor.value.trim();
    if (!value) {
        clearJsonEditorErrorState(editor);
        return;
    }

    try {
        editor.value = JSON.stringify(JSON.parse(value), null, 2);
        clearJsonEditorErrorState(editor);
    } catch {
        markJsonEditorInvalid(editor);
    }
};

const confirmModalElement = document.getElementById("confirmActionModal");
const confirmModalMessageElement = document.getElementById("confirmActionModalMessage");
const confirmModalTitleElement = document.getElementById("confirmActionModalLabel");
const confirmModalAcceptButton = document.getElementById("confirmActionModalAccept");

let confirmActionModal = null;
let pendingConfirmElement = null;

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
    if (confirmModalTitleElement instanceof HTMLElement) {
        confirmModalTitleElement.textContent = "Confirm action";
    }
    if (confirmModalMessageElement instanceof HTMLElement) {
        confirmModalMessageElement.textContent = "Continue?";
    }
    if (confirmModalAcceptButton instanceof HTMLButtonElement) {
        confirmModalAcceptButton.textContent = "Continue";
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
    confirmModalElement.addEventListener("hidden.bs.modal", resetPendingConfirm);
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

    const message = button.getAttribute("data-confirm") || "Continue?";
    const modal = getConfirmActionModal();

    if (modal === null) {
        const form = button.closest("form");
        if (form instanceof HTMLFormElement) {
            form.requestSubmit(button instanceof HTMLButtonElement || button instanceof HTMLInputElement ? button : undefined);
        }
        return;
    }

    pendingConfirmElement = button;
    if (confirmModalTitleElement instanceof HTMLElement) {
        confirmModalTitleElement.textContent = button.getAttribute("data-confirm-title") || button.textContent?.trim() || "Confirm action";
    }
    if (confirmModalMessageElement instanceof HTMLElement) {
        confirmModalMessageElement.textContent = message;
    }
    if (confirmModalAcceptButton instanceof HTMLButtonElement) {
        confirmModalAcceptButton.textContent = button.getAttribute("data-confirm-accept") || button.textContent?.trim() || "Continue";
    }

    modal.show();
});

document.addEventListener(
    "blur",
    (event) => {
        if (!(event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLInputElement)) {
            return;
        }
        if (!event.target.matches("[data-json-editor]")) {
            return;
        }
        reformatJsonEditor(event.target);
    },
    true,
);

document.addEventListener("input", (event) => {
    if (!(event.target instanceof HTMLTextAreaElement || event.target instanceof HTMLInputElement)) {
        return;
    }
    if (!event.target.matches("[data-json-editor]")) {
        return;
    }
    clearJsonEditorErrorState(event.target);
});
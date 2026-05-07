//
// app/static/js/app.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

"use strict";

/**
 * Live update system using Server-Sent Events.
 * Automatically reloads the page when relevant resources change.
 */
const initializeLiveUpdates = () => {
    const currentPath = window.location.pathname;

    const relevantResources = {
        "/servers": ["server"],
        "/configs": ["config", "server"],
        "/domains": ["domain", "server"],
        "/users": ["user"],
        "/api-keys": ["api_key"],
        "/audit-logs": ["audit_log", "server", "config", "domain", "user", "api_key"],
        "/": ["server", "config", "audit_log"],
    };

    const getRelevantTypes = () => {
        const segments = currentPath.split("/").filter(Boolean);
        const activeBaseSegment = segments.length > 0 ? `/${segments[0]}` : "/";
        return new Set(relevantResources[activeBaseSegment] || []);
    };

    const relevantTypes = getRelevantTypes();
    if (relevantTypes.size === 0) {
        return;
    }

    let eventSource = null;
    let reconnectAttempts = 0;
    const maxReconnectAttempts = 5;
    const baseReconnectDelay = 1000;
    const maxReconnectDelay = 30000;
    const reloadDebounceDelay = 250;
    let reconnectTimeoutId = null;
    let reloadTimeoutId = null;
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

    const triggerCoalescedReload = () => {
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
        }, reloadDebounceDelay);
    };

    const scheduleReconnect = () => {
        if (document.hidden || reconnectAttempts >= maxReconnectAttempts) {
            return;
        }

        reconnectAttempts += 1;
        const delay = Math.min(baseReconnectDelay * Math.pow(2, reconnectAttempts - 1), maxReconnectDelay);
        clearReconnectTimeout();
        reconnectTimeoutId = window.setTimeout(() => {
            reconnectTimeoutId = null;
            connect();
        }, delay);
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

        eventSource.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                if (relevantTypes.has(data.type)) {
                    triggerCoalescedReload();
                }
            } catch {
                // Ignore malformed events
            }
        };

        eventSource.onerror = () => {
            if (eventSource !== null) {
                eventSource.close();
                eventSource = null;
            }

            scheduleReconnect();
        };
    };

    const disconnect = () => {
        if (eventSource !== null) {
            eventSource.close();
            eventSource = null;
        }
        clearReconnectTimeout();
    };

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            if (reloadTimeoutId !== null) {
                reloadPendingWhileHidden = true;
                clearReloadTimeout();
            }
            disconnect();
        } else {
            if (reloadPendingWhileHidden) {
                triggerCoalescedReload();
                return;
            }
            connect();
        }
    });

    window.addEventListener("beforeunload", () => {
        clearReloadTimeout();
        disconnect();
    });

    connect();
};

initializeLiveUpdates();

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
    previousActiveConfirmElement = button instanceof HTMLElement
        ? button
        : (document.activeElement instanceof HTMLElement ? document.activeElement : null);
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

const initializeAuditLogFilters = () => {
    const searchInput = document.getElementById("filterSearch");
    const usernameSelect = document.getElementById("filterUsername");
    const dateFromInput = document.getElementById("filterDateFrom");
    const dateToInput = document.getElementById("filterDateTo");
    const resetButton = document.getElementById("filterReset");
    const tableBody = document.getElementById("auditTableBody");
    const visibleCountElement = document.getElementById("visibleCount");
    const totalCountElement = document.getElementById("totalCount");
    const loadMoreSentinel = document.getElementById("loadMoreSentinel");

    if (
        !(searchInput instanceof HTMLInputElement) ||
        !(usernameSelect instanceof HTMLSelectElement) ||
        !(dateFromInput instanceof HTMLInputElement) ||
        !(dateToInput instanceof HTMLInputElement) ||
        !(resetButton instanceof HTMLButtonElement) ||
        !(tableBody instanceof HTMLTableSectionElement) ||
        !(visibleCountElement instanceof HTMLElement) ||
        !(totalCountElement instanceof HTMLElement)
    ) {
        return;
    }

    // The audit logs page now owns its own paginated filtering logic.
    if (loadMoreSentinel instanceof HTMLElement) {
        return;
    }

    const parseTimestampEpoch = (value) => {
        if (typeof value !== "string" || !value.trim()) {
            return Number.NaN;
        }

        const numericValue = Number(value);
        if (Number.isFinite(numericValue)) {
            return numericValue;
        }

        const parsedValue = Date.parse(value);
        return Number.isFinite(parsedValue) ? parsedValue : Number.NaN;
    };

    const parseDateInputBoundary = (value, endOfDay = false) => {
        const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
        if (match === null) {
            return null;
        }

        const year = Number.parseInt(match[1], 10);
        const month = Number.parseInt(match[2], 10) - 1;
        const day = Number.parseInt(match[3], 10);
        return new Date(
            year,
            month,
            day,
            endOfDay ? 23 : 0,
            endOfDay ? 59 : 0,
            endOfDay ? 59 : 0,
            endOfDay ? 999 : 0,
        ).getTime();
    };

    const normalizeText = (value) => String(value || "").toLowerCase().trim();

    const rows = Array.from(tableBody.querySelectorAll("tr[data-timestamp]")).map((row) => {
        const cellText = Array.from(row.cells, (cell) => normalizeText(cell.textContent)).join(" ");
        const searchBlob = [
            row.dataset.action,
            row.dataset.username,
            row.dataset.resource,
            row.dataset.details,
            cellText,
        ].map(normalizeText).join(" ");

        return {
            element: row,
            searchBlob,
            timestampEpoch: parseTimestampEpoch(row.dataset.timestamp || ""),
            username: row.dataset.username || "",
        };
    });

    const usernames = [...new Set(rows.map((row) => row.username).filter(Boolean))].sort();
    const existingOptions = new Set(Array.from(usernameSelect.options, (option) => option.value));

    for (const username of usernames) {
        if (existingOptions.has(username)) {
            continue;
        }
        const option = document.createElement("option");
        option.value = username;
        option.textContent = username;
        usernameSelect.appendChild(option);
    }

    totalCountElement.textContent = String(rows.length);
    let filterTimeoutId = null;

    const updateNoResultsState = (visibleCount) => {
        const emptyRow = document.getElementById("emptyRow");
        if (emptyRow instanceof HTMLTableRowElement) {
            emptyRow.hidden = rows.length > 0;
        }

        let noResultsRow = document.getElementById("noResultsRow");
        if (visibleCount === 0 && rows.length > 0) {
            if (!(noResultsRow instanceof HTMLTableRowElement)) {
                noResultsRow = document.createElement("tr");
                noResultsRow.id = "noResultsRow";

                const cell = document.createElement("td");
                cell.className = "text-body-secondary";
                cell.colSpan = 5;
                cell.textContent = "No entries match the current filters.";

                noResultsRow.appendChild(cell);
                tableBody.appendChild(noResultsRow);
            }
            noResultsRow.hidden = false;
        } else if (noResultsRow instanceof HTMLTableRowElement) {
            noResultsRow.hidden = true;
        }
    };

    const filterRows = () => {
        filterTimeoutId = null;
        const searchTerm = normalizeText(searchInput.value);
        const selectedUsername = usernameSelect.value;
        const dateFrom = dateFromInput.value ? parseDateInputBoundary(dateFromInput.value) : null;
        const dateTo = dateToInput.value ? parseDateInputBoundary(dateToInput.value, true) : null;

        let visibleCount = 0;

        for (const row of rows) {
            const matchesSearch = !searchTerm || row.searchBlob.includes(searchTerm);
            const matchesUsername = !selectedUsername || row.username === selectedUsername;

            let matchesDate = true;
            if (!Number.isNaN(row.timestampEpoch)) {
                if (dateFrom !== null && row.timestampEpoch < dateFrom) {
                    matchesDate = false;
                }
                if (dateTo !== null && row.timestampEpoch > dateTo) {
                    matchesDate = false;
                }
            }

            const isVisible = matchesSearch && matchesUsername && matchesDate;
            row.element.hidden = !isVisible;
            if (isVisible) {
                visibleCount += 1;
            }
        }

        visibleCountElement.textContent = String(visibleCount);
        updateNoResultsState(visibleCount);
    };

    const scheduleFilter = () => {
        if (filterTimeoutId !== null) {
            window.clearTimeout(filterTimeoutId);
        }
        filterTimeoutId = window.setTimeout(filterRows, 120);
    };

    const resetFilters = () => {
        if (filterTimeoutId !== null) {
            window.clearTimeout(filterTimeoutId);
            filterTimeoutId = null;
        }
        searchInput.value = "";
        usernameSelect.value = "";
        dateFromInput.value = "";
        dateToInput.value = "";
        filterRows();
    };

    searchInput.addEventListener("input", scheduleFilter);
    usernameSelect.addEventListener("change", filterRows);
    dateFromInput.addEventListener("change", filterRows);
    dateToInput.addEventListener("change", filterRows);
    resetButton.addEventListener("click", resetFilters);

    filterRows();
};

initializeAuditLogFilters();

const initializeDomainPreview = () => {
    const form = document.querySelector("form[data-domain-preview-form]");
    const previewOutput = document.querySelector("[data-domain-preview-output]");
    const errorContainer = document.querySelector("[data-domain-preview-errors-container]");
    const errorList = document.querySelector("[data-domain-preview-errors]");

    if (
        !(form instanceof HTMLFormElement) ||
        !(previewOutput instanceof HTMLElement) ||
        !(errorContainer instanceof HTMLElement) ||
        !(errorList instanceof HTMLUListElement)
    ) {
        return;
    }

    const previewUrl = form.dataset.domainPreviewUrl;
    if (!previewUrl) {
        return;
    }

    const fieldErrorPrefixes = new Map([
        ["Reverse proxy options require an upstream target.", ["upstream", "reverse_proxy_options"]],
        ["Reverse proxy options", ["reverse_proxy_options"]],
        ["Encode settings", ["encode_directives"]],
        ["Header block", ["header_directives"]],
        ["Request body block", ["request_body_directives"]],
        ["Log block", ["log_directives"]],
        ["TLS block", ["tls_directives"]],
        ["Basic auth block", ["basic_auth_directives"]],
        ["Additional custom directives", ["caddy_directives"]],
    ]);

    const fieldsByName = new Map();
    for (const field of form.elements) {
        if (
            (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement) &&
            field.name
        ) {
            fieldsByName.set(field.name, field);
        }
    }

    let previewTimeoutId = null;
    let activeRequestController = null;
    let latestRequestId = 0;

    const clearInvalidStates = () => {
        for (const field of fieldsByName.values()) {
            field.classList.remove("is-invalid");
            field.removeAttribute("aria-invalid");
        }
    };

    const markInvalidFields = (errors) => {
        clearInvalidStates();

        for (const error of errors) {
            for (const [prefix, fieldNames] of fieldErrorPrefixes.entries()) {
                if (!error.startsWith(prefix)) {
                    continue;
                }
                for (const fieldName of fieldNames) {
                    const field = fieldsByName.get(fieldName);
                    if (!field) {
                        continue;
                    }
                    field.classList.add("is-invalid");
                    field.setAttribute("aria-invalid", "true");
                }
            }
        }
    };

    const renderErrors = (errors) => {
        errorList.replaceChildren();
        if (!Array.isArray(errors) || errors.length === 0) {
            errorContainer.hidden = true;
            clearInvalidStates();
            return;
        }

        for (const error of errors) {
            const item = document.createElement("li");
            item.textContent = error;
            errorList.appendChild(item);
        }

        markInvalidFields(errors);
        errorContainer.hidden = false;
    };

    const fetchPreview = async () => {
        latestRequestId += 1;
        const requestId = latestRequestId;

        activeRequestController?.abort();
        activeRequestController = new AbortController();

        try {
            const response = await fetch(previewUrl, {
                method: "POST",
                body: new FormData(form),
                headers: { Accept: "application/json" },
                signal: activeRequestController.signal,
            });

            if (!response.ok) {
                throw new Error(`Preview request failed with status ${response.status}`);
            }

            const payload = await response.json();
            if (requestId !== latestRequestId) {
                return;
            }

            previewOutput.textContent = typeof payload.preview === "string"
                ? payload.preview
                : previewOutput.textContent;
            renderErrors(Array.isArray(payload.errors) ? payload.errors : []);
        } catch (error) {
            if (error instanceof DOMException && error.name === "AbortError") {
                return;
            }

            renderErrors(["Live preview is temporarily unavailable. You can still save once the form validates."]);
        }
    };

    const schedulePreview = () => {
        if (previewTimeoutId !== null) {
            window.clearTimeout(previewTimeoutId);
        }
        previewTimeoutId = window.setTimeout(() => {
            previewTimeoutId = null;
            void fetchPreview();
        }, 150);
    };

    form.addEventListener("input", schedulePreview);
    form.addEventListener("change", schedulePreview);

    void fetchPreview();
};

initializeDomainPreview();

const initializeTagInputs = () => {
    const tagInputs = document.querySelectorAll("input[data-tag-input]");

    for (const field of tagInputs) {
        if (!(field instanceof HTMLInputElement) || field.dataset.tagInputEnhanced === "true") {
            continue;
        }

        const formGroup = field.parentElement;
        if (!(formGroup instanceof HTMLElement)) {
            continue;
        }

        field.dataset.tagInputEnhanced = "true";

        const normalizeTag = (value) => value.trim().replaceAll(/\s+/g, " ");
        const parseTags = (rawValue) => [...new Set(
            rawValue
                .split(/[\s,]+/)
                .map(normalizeTag)
                .filter(Boolean),
        )];

        let tags = parseTags(field.value);

        const wrapper = document.createElement("div");
        wrapper.className = "tag-input";

        const list = document.createElement("div");
        list.className = "tag-input__list";
        list.setAttribute("aria-live", "polite");

        const editor = document.createElement("input");
        editor.type = "text";
        editor.className = field.className;
        editor.id = field.id;
        editor.placeholder = field.placeholder;
        editor.autocomplete = "off";
        editor.setAttribute("aria-describedby", field.getAttribute("aria-describedby") || "");

        field.removeAttribute("id");
        field.type = "hidden";

        const syncField = () => {
            field.value = tags.join(", ");
        };

        const renderTags = () => {
            list.replaceChildren();

            for (const tag of tags) {
                const item = document.createElement("span");
                item.className = "tag-input__token";

                const label = document.createElement("span");
                label.textContent = tag;

                const removeButton = document.createElement("button");
                removeButton.type = "button";
                removeButton.className = "tag-input__remove";
                removeButton.setAttribute("aria-label", `Remove tag ${tag}`);
                removeButton.textContent = "x";
                removeButton.addEventListener("click", () => {
                    tags = tags.filter((entry) => entry !== tag);
                    syncField();
                    renderTags();
                    editor.focus();
                });

                item.append(label, removeButton);
                list.appendChild(item);
            }

            list.hidden = tags.length === 0;
        };

        const addTagsFromValue = (rawValue) => {
            const parsed = parseTags(rawValue);
            if (parsed.length === 0) {
                return false;
            }

            let didChange = false;
            for (const tag of parsed) {
                if (tags.includes(tag)) {
                    continue;
                }
                tags.push(tag);
                didChange = true;
            }

            if (didChange) {
                syncField();
                renderTags();
            }
            return didChange;
        };

        const commitEditorValue = () => {
            const rawValue = editor.value;
            if (!rawValue.trim()) {
                editor.value = "";
                return;
            }
            addTagsFromValue(rawValue);
            editor.value = "";
        };

        editor.addEventListener("keydown", (event) => {
            const shouldCommit = event.key === "Tab" || event.key === "," || event.key === " ";

            if (shouldCommit && editor.value.trim()) {
                event.preventDefault();
                commitEditorValue();
                return;
            }

            if (event.key === "Backspace" && !editor.value && tags.length > 0) {
                tags = tags.slice(0, -1);
                syncField();
                renderTags();
            }
        });

        editor.addEventListener("blur", commitEditorValue);

        editor.addEventListener("paste", (event) => {
            const clipboardText = event.clipboardData?.getData("text") || "";
            if (!/[\s,]/.test(clipboardText)) {
                return;
            }

            event.preventDefault();
            addTagsFromValue(clipboardText);
            editor.value = "";
        });

        wrapper.append(editor, list);
        formGroup.insertBefore(wrapper, field.nextSibling);

        syncField();
        renderTags();
    }
};

initializeTagInputs();
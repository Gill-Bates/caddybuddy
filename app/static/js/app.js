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
        "/templates": ["config_template", "site"],
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

/**
 * Mobile sidebar slide-in menu.
 * Opens/closes the sidebar on mobile via hamburger button, backdrop click, or nav link selection.
 */
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

    const openMenu = () => {
        sidebar.classList.add("is-open");
        backdrop.classList.add("is-visible");
        toggle.classList.add("is-active");
        toggle.setAttribute("aria-expanded", "true");
        toggle.setAttribute("aria-label", "Close menu");
        document.body.classList.add("app-body--menu-open");
    };

    const closeMenu = () => {
        sidebar.classList.remove("is-open");
        backdrop.classList.remove("is-visible");
        toggle.classList.remove("is-active");
        toggle.setAttribute("aria-expanded", "false");
        toggle.setAttribute("aria-label", "Open menu");
        document.body.classList.remove("app-body--menu-open");
    };

    const toggleMenu = () => {
        if (sidebar.classList.contains("is-open")) {
            closeMenu();
        } else {
            openMenu();
        }
    };

    toggle.addEventListener("click", toggleMenu);
    backdrop.addEventListener("click", closeMenu);

    // Close menu when a nav link is clicked
    sidebar.addEventListener("click", (event) => {
        if (!(event.target instanceof HTMLElement)) {
            return;
        }
        const link = event.target.closest("a.app-nav__link, button[type='submit']");
        if (link !== null) {
            closeMenu();
        }
    });

    // Close menu on Escape key
    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && sidebar.classList.contains("is-open")) {
            closeMenu();
            toggle.focus();
        }
    });

    // Close menu when resizing to desktop viewport
    const mediaQuery = window.matchMedia("(min-width: 992px)");
    mediaQuery.addEventListener("change", (event) => {
        if (event.matches && sidebar.classList.contains("is-open")) {
            closeMenu();
        }
    });
};

initializeMobileMenu();

/**
 * Queue badge counter.
 * Fetches the pending deployment count and updates the sidebar badge.
 */
const initializeQueueBadge = () => {
    const badge = document.getElementById("queueBadge");
    if (!(badge instanceof HTMLElement)) {
        return;
    }

    const refreshInterval = 60000;
    const requestTimeout = 10000;
    let intervalId = null;
    let updateInFlight = false;
    let activeRequestController = null;

    const startRefreshLoop = () => {
        if (intervalId !== null) {
            return;
        }
        intervalId = window.setInterval(() => {
            void updateBadge();
        }, refreshInterval);
    };

    const stopRefreshLoop = () => {
        if (intervalId !== null) {
            window.clearInterval(intervalId);
            intervalId = null;
        }
    };

    const updateBadge = async () => {
        if (updateInFlight) {
            return;
        }

        const controller = new AbortController();
        const timeoutId = window.setTimeout(() => controller.abort(), requestTimeout);
        updateInFlight = true;
        activeRequestController = controller;

        try {
            const response = await fetch("/api/v1/queue/count", {
                credentials: "same-origin",
                signal: controller.signal,
            });
            if (!response.ok) {
                badge.hidden = true;
                return;
            }
            const data = await response.json();
            const count = Number(data.count) || 0;
            if (count > 0) {
                badge.textContent = count > 99 ? "99+" : String(count);
                badge.hidden = false;
            } else {
                badge.hidden = true;
            }
        } catch {
            badge.hidden = true;
        } finally {
            window.clearTimeout(timeoutId);
            if (activeRequestController === controller) {
                activeRequestController = null;
            }
            updateInFlight = false;
        }
    };

    void updateBadge();
    startRefreshLoop();

    document.addEventListener("visibilitychange", () => {
        if (document.hidden) {
            activeRequestController?.abort();
            stopRefreshLoop();
        } else {
            void updateBadge();
            startRefreshLoop();
        }
    });
};

initializeQueueBadge();

const initializeQueueDeploySelection = () => {
    const siteSelect = document.getElementById("queue-site-id");
    const serverSelect = document.getElementById("queue-server-id");
    const selectedSiteName = document.getElementById("queueSelectedSiteName");
    const rowButtons = document.querySelectorAll(".js-queue-select-site");

    if (
        !(siteSelect instanceof HTMLSelectElement) ||
        !(serverSelect instanceof HTMLSelectElement) ||
        !(selectedSiteName instanceof HTMLElement)
    ) {
        return;
    }

    const syncSelectedSiteLabel = () => {
        const selectedOption = siteSelect.selectedOptions.item(0);
        if (!(selectedOption instanceof HTMLOptionElement)) {
            return;
        }
        selectedSiteName.textContent = selectedOption.dataset.siteDomain || selectedOption.textContent?.trim() || "selected site";
    };

    siteSelect.addEventListener("change", syncSelectedSiteLabel);

    for (const button of rowButtons) {
        if (!(button instanceof HTMLButtonElement)) {
            continue;
        }

        button.addEventListener("click", () => {
            const siteId = button.dataset.siteId;
            if (typeof siteId !== "string" || !siteId) {
                return;
            }

            siteSelect.value = siteId;
            syncSelectedSiteLabel();
            siteSelect.scrollIntoView({ behavior: "smooth", block: "center" });
            serverSelect.focus();
        });
    }

    syncSelectedSiteLabel();
};

initializeQueueDeploySelection();

const initializeLoadingSubmitForms = () => {
    const forms = document.querySelectorAll("form[data-loading-submit-form]");

    for (const form of forms) {
        if (!(form instanceof HTMLFormElement)) {
            continue;
        }

        form.addEventListener("submit", (event) => {
            if (event.defaultPrevented) {
                return;
            }

            const submitter = event.submitter;
            if (
                !(submitter instanceof HTMLElement) ||
                !submitter.matches("button, input[type='submit'], input[type='image']")
            ) {
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

initializeLoadingSubmitForms();

const initializeAutoDismissAlerts = () => {
    const alerts = document.querySelectorAll("[data-auto-dismiss-alert]");

    for (const alertElement of alerts) {
        if (!(alertElement instanceof HTMLElement)) {
            continue;
        }

        const delayValue = Number.parseInt(alertElement.dataset.autoDismissDelay || "5000", 10);
        const delay = Number.isFinite(delayValue) && delayValue > 0 ? delayValue : 5000;
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
            window.setTimeout(() => {
                alertElement.remove();
            }, 150);
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

initializeAutoDismissAlerts();

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
const DEFAULT_CONFIRM_ACCEPT_CLASS = "btn btn-primary";

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
        confirmModalAcceptButton.className = DEFAULT_CONFIRM_ACCEPT_CLASS;
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
        confirmModalAcceptButton.className = `btn ${button.getAttribute("data-confirm-btn-class") || "btn-primary"}`;
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
        return Date.UTC(
            year,
            month,
            day,
            endOfDay ? 23 : 0,
            endOfDay ? 59 : 0,
            endOfDay ? 59 : 0,
            endOfDay ? 999 : 0,
        );
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

    if (rows.length > 2000) {
        console.warn("Client-side audit log filtering disabled: row count exceeds safe threshold");
        return;
    }

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
    const statusBadge = document.querySelector("[data-domain-preview-status]");
    const presetsContainer = document.getElementById("domain-security-presets");
    const headerCombined = document.getElementById("domain-header-combined");
    const headerExtra = document.getElementById("domain-header-extra");

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

    // Security preset checkboxes → combined header_directives hidden field.
    const presetCheckboxes = presetsContainer
        ? Array.from(presetsContainer.querySelectorAll("input[data-header-line]"))
        : [];

    const syncHeaderDirectives = () => {
        if (!headerCombined) return;
        const presetLines = presetCheckboxes
            .filter((cb) => cb.checked)
            .map((cb) => cb.dataset.headerLine ?? "");
        const extraLines = headerExtra
            ? headerExtra.value.split("\n").filter((l) => l.trim())
            : [];
        headerCombined.value = [...presetLines, ...extraLines].join("\n");
    };

    // Parse existing header_directives to pre-check matching presets.
    if (presetCheckboxes.length > 0 && headerCombined) {
        const existingLines = headerCombined.value.split("\n").map((l) => l.trim()).filter(Boolean);
        const remainingLines = [];
        for (const line of existingLines) {
            const matchedCb = presetCheckboxes.find((cb) => cb.dataset.headerLine === line);
            if (matchedCb) {
                matchedCb.checked = true;
            } else {
                remainingLines.push(line);
            }
        }
        if (headerExtra) {
            headerExtra.value = remainingLines.join("\n");
        }
    }

    const updateStatus = (errors) => {
        if (!(statusBadge instanceof HTMLElement)) return;
        if (!Array.isArray(errors) || errors.length === 0) {
            statusBadge.textContent = "Valid";
            statusBadge.className = "badge text-bg-success";
        } else {
            statusBadge.textContent = `${errors.length} warning${errors.length !== 1 ? "s" : ""}`;
            statusBadge.className = "badge text-bg-warning";
        }
    };

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
        updateStatus(errors);
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
        let previewTimedOut = false;
        const timeoutId = window.setTimeout(() => {
            previewTimedOut = true;
            activeRequestController?.abort();
        }, 10000);

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
            if (error instanceof DOMException && error.name === "AbortError" && !previewTimedOut) {
                return;
            }

            renderErrors(["Live preview is temporarily unavailable. You can still save once the form validates."]);
        } finally {
            window.clearTimeout(timeoutId);
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

    // Preset checkboxes and extra textarea trigger header sync + preview refresh.
    for (const cb of presetCheckboxes) {
        cb.addEventListener("change", () => {
            syncHeaderDirectives();
            schedulePreview();
        });
    }
    if (headerExtra instanceof HTMLTextAreaElement) {
        headerExtra.addEventListener("input", () => {
            syncHeaderDirectives();
            schedulePreview();
        });
    }

    // Bootstrap HTML5 inline validation on submit.
    form.addEventListener("submit", (event) => {
        syncHeaderDirectives();
        if (!form.checkValidity()) {
            event.preventDefault();
            event.stopPropagation();
        }
        form.classList.add("was-validated");
    });

    syncHeaderDirectives();
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
        list.setAttribute("role", "list");
        list.setAttribute("aria-label", "Selected tags");

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
                item.setAttribute("role", "listitem");

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

        const form = field.form;
        if (form instanceof HTMLFormElement) {
            form.addEventListener("submit", commitEditorValue);
        }

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
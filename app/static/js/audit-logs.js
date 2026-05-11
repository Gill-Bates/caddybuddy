//
// app/static/js/audit-logs.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

// Paginated audit log viewer with infinite scroll and filter support.

const PAGE_SIZE = 50;
const DEBOUNCE_MS = 300;

const feedContainer = document.getElementById("auditTableBody");
const loadingRow = document.getElementById("loadingRow");
const loadMoreSentinel = document.getElementById("loadMoreSentinel");
const visibleCountEl = document.getElementById("visibleCount");
const totalCountEl = document.getElementById("totalCount");
const tableContainer = document.getElementById("auditTableContainer");

const filterSearch = document.getElementById("filterSearch");
const filterUsername = document.getElementById("filterUsername");
const filterDateFrom = document.getElementById("filterDateFrom");
const filterDateTo = document.getElementById("filterDateTo");
const filterReset = document.getElementById("filterReset");

let currentOffset = 0;
let totalCount = 0;
let isLoading = false;
let hasMore = true;
let debounceTimer = null;
let currentGeneration = 0;
let currentAbortController = null;

// ── Action classification ──────────────────────────────────────────────

const ACTION_META = {
    login_success: { label: "Login", icon: "🔓", severity: "success" },
    login_failed: { label: "Login Failed", icon: "🚫", severity: "danger" },
    logout: { label: "Logout", icon: "🔒", severity: "neutral" },
    user_created: { label: "User Created", icon: "👤", severity: "info" },
    profile_updated: { label: "Profile Updated", icon: "✏️", severity: "info" },
    password_changed: { label: "Password Changed", icon: "🔑", severity: "warning" },
    server_created: { label: "Server Created", icon: "🖥️", severity: "success" },
    server_tested: { label: "Server Tested", icon: "🧪", severity: "info" },
    server_synced: { label: "Server Synced", icon: "🔄", severity: "info" },
    server_deleted: { label: "Server Deleted", icon: "🗑️", severity: "danger" },
    api_key_created: { label: "API Key Created", icon: "🔐", severity: "info" },
    api_key_toggled: { label: "API Key Toggled", icon: "🔀", severity: "warning" },
    create: { label: "Created", icon: "➕", severity: "success" },
    update: { label: "Updated", icon: "✏️", severity: "info" },
    delete: { label: "Deleted", icon: "🗑️", severity: "danger" },
    deploy: { label: "Deployed", icon: "🚀", severity: "success" },
    deploy_batch: { label: "Batch Deploy", icon: "🚀", severity: "success" },
    retry: { label: "Retried", icon: "🔄", severity: "warning" },
    rollback: { label: "Rollback", icon: "⏪", severity: "warning" },
};

/**
 * Returns action metadata with fallback for unknown actions.
 * @param {string} action
 * @returns {{ label: string, icon: string, severity: string }}
 */
function getActionMeta(action) {
    if (ACTION_META[action]) return ACTION_META[action];
    const label = action
        .replace(/_/g, " ")
        .replace(/\b\w/g, c => c.toUpperCase());
    return { label, icon: "📋", severity: "neutral" };
}

// ── Relative time ──────────────────────────────────────────────────────

/**
 * Formats a timestamp into relative human time and absolute tooltip.
 * @param {string} iso
 * @param {string} formatted
 * @returns {{ relative: string, absolute: string }}
 */
function formatTime(iso, formatted) {
    if (!iso) return { relative: "—", absolute: "" };

    const date = new Date(iso);
    const now = new Date();
    const diffMs = now - date;
    const diffSec = Math.floor(diffMs / 1000);
    const diffMin = Math.floor(diffSec / 60);
    const diffHour = Math.floor(diffMin / 60);
    const diffDay = Math.floor(diffHour / 24);

    let relative;
    if (diffSec < 60) relative = "just now";
    else if (diffMin < 60) relative = `${diffMin}m ago`;
    else if (diffHour < 24) relative = `${diffHour}h ago`;
    else if (diffDay < 7) relative = `${diffDay}d ago`;
    else relative = formatted;

    return { relative, absolute: formatted };
}

// ── Smart detail summary ───────────────────────────────────────────────

/**
 * Creates a concise, human-readable summary from the details object,
 * tailored to the action type. Falls back to key=value pairs.
 * @param {string} action
 * @param {object|null} details
 * @param {string} resource
 * @returns {string}
 */
function smartSummary(action, details, resource) {
    if (!details || typeof details !== "object" || Array.isArray(details)) return "";

    const parts = [];

    // Extract meaningful fields based on action
    if (details.username && action !== "login_success") {
        parts.push(details.username);
    }
    if (details.domain) {
        parts.push(details.domain);
    }
    if (details.server_name) {
        parts.push(details.server_name);
    }
    if (details.name && !details.domain && !details.server_name) {
        parts.push(details.name);
    }
    if (details.status) {
        parts.push(details.status);
    }
    if (details.reason) {
        parts.push(details.reason);
    }
    if (details.error) {
        parts.push(details.error);
    }

    if (parts.length > 0) return parts.join(" · ");

    // Fallback: compact key=value for remaining keys, skip long values
    const entries = Object.entries(details);
    if (entries.length === 0) return "";

    const compact = entries
        .filter(([, v]) => v !== null && v !== undefined && String(v).length < 80)
        .map(([k, v]) => `${k}: ${v}`)
        .slice(0, 3);

    return compact.join(" · ");
}

/**
 * Formats an entry's details object as indented JSON for the expanded view.
 * @param {unknown} details
 * @returns {string}
 */
function formatDetails(details) {
    if (!details || typeof details !== "object") return "";
    try {
        return JSON.stringify(details, null, 2);
    } catch {
        return String(details);
    }
}

// ── Row rendering ──────────────────────────────────────────────────────

/**
 * Creates a feed item for an audit log entry.
 * @param {{
 *   id: number | string,
 *   timestamp: string,
 *   timestamp_iso: string,
 *   action: string,
 *   username: string,
 *   resource: string,
 *   details: unknown,
 * }} entry
 * @returns {HTMLElement}
 */
function createRow(entry) {
    const meta = getActionMeta(entry.action);
    const time = formatTime(entry.timestamp_iso, entry.timestamp);
    const summary = smartSummary(entry.action, entry.details, entry.resource);
    const fullDetails = formatDetails(entry.details);

    const row = document.createElement("div");
    row.className = `audit-row audit-row--${meta.severity}`;
    row.dataset.timestamp = entry.timestamp_iso;
    row.dataset.action = entry.action;
    row.dataset.username = entry.username;
    row.dataset.resource = entry.resource;

    // Severity indicator
    const indicator = document.createElement("div");
    indicator.className = "audit-row__indicator";
    indicator.setAttribute("aria-hidden", "true");

    // Main content
    const content = document.createElement("div");
    content.className = "audit-row__content";

    // Top line: action label + timestamp
    const topLine = document.createElement("div");
    topLine.className = "audit-row__top";

    const actionEl = document.createElement("span");
    actionEl.className = "audit-row__action";

    const iconSpan = document.createElement("span");
    iconSpan.className = "audit-row__icon";
    iconSpan.textContent = meta.icon;
    iconSpan.setAttribute("aria-hidden", "true");

    const labelSpan = document.createElement("span");
    labelSpan.className = "audit-row__label";
    labelSpan.textContent = meta.label;

    actionEl.append(iconSpan, labelSpan);

    // Resource tag (if meaningful)
    if (entry.resource && entry.resource !== "user") {
        const resourceTag = document.createElement("span");
        resourceTag.className = "audit-row__resource";
        resourceTag.textContent = entry.resource;
        actionEl.appendChild(resourceTag);
    }

    const timeEl = document.createElement("time");
    timeEl.className = "audit-row__time";
    timeEl.dateTime = entry.timestamp_iso;
    timeEl.textContent = time.relative;
    timeEl.title = time.absolute;

    topLine.append(actionEl, timeEl);

    // Bottom line: user + summary
    const bottomLine = document.createElement("div");
    bottomLine.className = "audit-row__bottom";

    const userEl = document.createElement("span");
    userEl.className = "audit-row__user";
    userEl.textContent = entry.username;

    bottomLine.appendChild(userEl);

    if (summary) {
        const sep = document.createElement("span");
        sep.className = "audit-row__sep";
        sep.textContent = "—";
        sep.setAttribute("aria-hidden", "true");

        const summaryEl = document.createElement("span");
        summaryEl.className = "audit-row__summary";
        summaryEl.textContent = summary;

        bottomLine.append(sep, summaryEl);
    }

    content.append(topLine, bottomLine);

    // Expandable details (only if there's content)
    if (fullDetails) {
        const detailsId = `audit-details-${entry.id}`;

        row.setAttribute("role", "button");
        row.setAttribute("tabindex", "0");
        row.setAttribute("aria-expanded", "false");
        row.setAttribute("aria-controls", detailsId);

        const expandIcon = document.createElement("span");
        expandIcon.className = "audit-row__expand";
        expandIcon.textContent = "›";
        expandIcon.setAttribute("aria-hidden", "true");

        const pre = document.createElement("pre");
        pre.id = detailsId;
        pre.className = "audit-row__details";
        pre.textContent = fullDetails;
        pre.hidden = true;

        const toggle = () => {
            const expanded = row.getAttribute("aria-expanded") === "true";
            row.setAttribute("aria-expanded", String(!expanded));
            pre.hidden = expanded;
        };

        row.addEventListener("click", toggle);
        row.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                toggle();
            }
        });

        content.appendChild(pre);
        row.append(indicator, content, expandIcon);
    } else {
        row.append(indicator, content);
    }

    return row;
}

// ── Status / empty states ──────────────────────────────────────────────

/**
 * Renders a status message in the feed.
 * @param {string} message
 * @param {"empty"|"error"} type
 */
function renderStatusRow(message, type = "empty") {
    const div = document.createElement("div");
    div.className = type === "error"
        ? "audit-feed__status audit-feed__status--error"
        : "audit-feed__status";
    div.textContent = message;
    feedContainer.appendChild(div);
}

/** Shows the no-results state. */
function showEmptyState() {
    renderStatusRow("No matching entries found.");
}

// ── Filters ────────────────────────────────────────────────────────────

/**
 * Returns the current filter values from the UI controls.
 * @returns {{ search: string, username: string, date_from: string, date_to: string }}
 */
function getFilters() {
    return {
        search: filterSearch.value.trim(),
        username: filterUsername.value,
        date_from: filterDateFrom.value,
        date_to: filterDateTo.value,
    };
}

// ── Data loading ───────────────────────────────────────────────────────

/**
 * Loads audit log entries from the API.
 * On reset, cancels any in-flight request and clears the current results.
 * Stale responses from superseded requests are silently discarded via generation counter.
 * @param {boolean} reset - Whether to reset pagination and start from offset 0.
 */
async function loadEntries(reset = false) {
    if (isLoading && !reset) return;
    if (!reset && !hasMore) return;

    const generation = ++currentGeneration;

    if (reset) {
        currentAbortController?.abort();
        currentAbortController = new AbortController();
        currentOffset = 0;
        hasMore = true;
        totalCount = 0;
        feedContainer.replaceChildren(loadingRow);
        loadingRow.classList.remove("d-none");
    }

    if (!currentAbortController) {
        currentAbortController = new AbortController();
    }

    const { signal } = currentAbortController;
    isLoading = true;
    loadMoreSentinel.classList.remove("d-none");

    const filters = getFilters();
    const params = new URLSearchParams({
        offset: String(currentOffset),
        limit: String(PAGE_SIZE),
    });
    for (const [key, value] of Object.entries(filters)) {
        if (value) params.set(key, value);
    }

    const requestUrl = new URL("./api/audit-logs", window.location.href);
    requestUrl.search = params.toString();

    try {
        const response = await fetch(requestUrl, { signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        const entries = Array.isArray(data.entries) ? data.entries : [];

        // Discard stale response from a superseded request.
        if (generation !== currentGeneration) return;

        totalCount = typeof data.total === "number" ? data.total : 0;
        hasMore = data.has_more === true;
        currentOffset += entries.length;

        if (reset) loadingRow.remove();

        if (entries.length === 0 && currentOffset === 0) {
            showEmptyState();
        } else {
            const fragment = document.createDocumentFragment();
            for (const entry of entries) {
                fragment.appendChild(createRow(entry));
            }
            feedContainer.appendChild(fragment);
        }

        visibleCountEl.textContent = String(currentOffset);
        totalCountEl.textContent = String(totalCount);

    } catch (error) {
        if (generation !== currentGeneration || signal.aborted) return;
        console.error("Failed to load audit logs:", error);
        if (reset) {
            loadingRow.remove();
            renderStatusRow("Failed to load entries. Please refresh the page.", "error");
        } else {
            hasMore = false;
        }
    } finally {
        if (generation === currentGeneration) {
            isLoading = false;
            loadMoreSentinel.classList.toggle("d-none", !hasMore);
        }
    }
}

/** Debounces a full reload for search input events. */
function debouncedReload() {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => loadEntries(true), DEBOUNCE_MS);
}

/** Clears all filter controls and reloads from the beginning. */
function resetFilters() {
    filterSearch.value = "";
    filterUsername.value = "";
    filterDateFrom.value = "";
    filterDateTo.value = "";
    debouncedReload();
}

function setupInfiniteScroll() {
    loadMoreSentinel.setAttribute("aria-hidden", "true");
    const observer = new IntersectionObserver(
        (entries) => {
            if (entries[0].isIntersecting && !isLoading && hasMore) {
                loadEntries(false);
            }
        },
        { root: tableContainer, rootMargin: "100px" }
    );
    observer.observe(loadMoreSentinel);
}

// Event listeners.
filterSearch.addEventListener("input", debouncedReload);
for (const el of [filterUsername, filterDateFrom, filterDateTo]) {
    el.addEventListener("change", debouncedReload);
}
filterReset.addEventListener("click", resetFilters);

// Initial load.
loadEntries(true).finally(setupInfiniteScroll);

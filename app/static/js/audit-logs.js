//
// app/static/js/audit-logs.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

// Paginated audit log viewer with infinite scroll and filter support.

const PAGE_SIZE = 50;
const DEBOUNCE_MS = 300;
const COL_COUNT = 5;

const tableBody = document.getElementById("auditTableBody");
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

/**
 * Formats an entry's details object as indented JSON.
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

/**
 * Creates a table row for an audit log entry using DOM APIs (no innerHTML).
 * @param {object} entry
 * @returns {HTMLTableRowElement}
 */
function createRow(entry) {
    const tr = document.createElement("tr");
    tr.dataset.timestamp = entry.timestamp_iso;
    tr.dataset.action = entry.action;
    tr.dataset.username = entry.username;
    tr.dataset.resource = entry.resource;

    const tdTime = document.createElement("td");
    tdTime.textContent = entry.timestamp;

    const tdAction = document.createElement("td");
    const code = document.createElement("code");
    code.className = "text-body";
    code.textContent = entry.action;
    tdAction.appendChild(code);

    const tdUser = document.createElement("td");
    tdUser.textContent = entry.username;

    const tdRes = document.createElement("td");
    tdRes.textContent = entry.resource;

    const tdDetails = document.createElement("td");
    const pre = document.createElement("pre");
    pre.className = "audit-details audit-details--table mb-0";
    pre.textContent = formatDetails(entry.details);
    tdDetails.appendChild(pre);

    tr.append(tdTime, tdAction, tdUser, tdRes, tdDetails);
    return tr;
}

/**
 * Renders a full-width status row into the table body.
 * @param {string} message
 * @param {"empty"|"error"} type
 */
function renderStatusRow(message, type = "empty") {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = COL_COUNT;
    td.className = type === "error"
        ? "text-danger text-center py-4"
        : "text-body-secondary text-center py-4";
    td.textContent = message;
    tr.appendChild(td);
    tableBody.appendChild(tr);
}

/** Shows the no-results state. */
function showEmptyState() {
    renderStatusRow("No matching entries found.");
}

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
        tableBody.replaceChildren(loadingRow);
        loadingRow.classList.remove("d-none");
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

    try {
        const response = await fetch(`/api/audit-logs?${params}`, { signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();

        // Discard stale response from a superseded request.
        if (generation !== currentGeneration) return;

        totalCount = data.total;
        hasMore = data.has_more;
        currentOffset += data.entries.length;

        if (reset) loadingRow.remove();

        if (data.entries.length === 0 && currentOffset === 0) {
            showEmptyState();
        } else {
            const fragment = document.createDocumentFragment();
            for (const entry of data.entries) {
                fragment.appendChild(createRow(entry));
            }
            tableBody.appendChild(fragment);
        }

        visibleCountEl.textContent = String(currentOffset);
        totalCountEl.textContent = String(totalCount);

    } catch (error) {
        if (generation !== currentGeneration || signal.aborted) return;
        console.error("Failed to load audit logs:", error);
        if (reset) {
            loadingRow.remove();
            renderStatusRow("Failed to load entries. Please refresh the page.", "error");
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
    loadEntries(true);
}

// Intersection Observer for infinite scroll.
const observer = new IntersectionObserver(
    (entries) => {
        if (entries[0].isIntersecting && !isLoading && hasMore) {
            loadEntries(false);
        }
    },
    { root: tableContainer, rootMargin: "100px" }
);
observer.observe(loadMoreSentinel);

// Event listeners.
filterSearch.addEventListener("input", debouncedReload);
for (const el of [filterUsername, filterDateFrom, filterDateTo]) {
    el.addEventListener("change", () => loadEntries(true));
}
filterReset.addEventListener("click", resetFilters);

// Initial load.
loadEntries(true);

//
// app/static/js/ssllabs-filter.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

(() => {
    "use strict";

    const normalize = (value) => String(value ?? "")
        .trim()
        .replace(/\s+/g, " ")
        .toLowerCase();

    const AUTO_QUEUED_FLASH_TEXT = "Scan automatically queued.";

    const removeAutoQueuedFlash = () => {
        const toastBodies = Array.from(document.querySelectorAll(".toast .toast-body"))
            .filter((node) => node instanceof HTMLElement);

        for (const body of toastBodies) {
            if (normalize(body.textContent) !== normalize(AUTO_QUEUED_FLASH_TEXT)) {
                continue;
            }

            const toast = body.closest(".toast");
            if (toast instanceof HTMLElement) {
                toast.remove();
            }
        }
    };

    const initializeSslLabsFilter = () => {
        const root = document.querySelector("[data-ssllabs-filter-root]");
        if (!(root instanceof HTMLElement)) {
            return;
        }
        if (root.dataset.ssllabsFilterInitialized === "true") {
            return;
        }

        const searchInput = root.querySelector("[data-ssllabs-search]");
        const gradeSelect = root.querySelector("[data-ssllabs-grade-filter]");
        const clearFiltersButton = root.querySelector("[data-ssllabs-clear-filters]");
        const visibleCount = root.querySelector("[data-ssllabs-visible-count]");
        const visibleLabel = root.querySelector("[data-ssllabs-visible-label]");
        const emptyState = root.querySelector("[data-ssllabs-empty]");
        const quickFilterButtons = Array.from(root.querySelectorAll("[data-ssllabs-filter-preset]"))
            .filter((button) => button instanceof HTMLButtonElement);
        const presetCounts = Array.from(root.querySelectorAll("[data-ssllabs-preset-count]"))
            .filter((count) => count instanceof HTMLElement);
        let activePreset = "all";

        if (!(searchInput instanceof HTMLInputElement) || !(gradeSelect instanceof HTMLSelectElement)) {
            return;
        }

        if (visibleCount instanceof HTMLElement) {
            visibleCount.setAttribute("aria-live", "polite");
            visibleCount.setAttribute("aria-atomic", "true");
        }

        if (visibleLabel instanceof HTMLElement) {
            visibleLabel.setAttribute("aria-live", "polite");
            visibleLabel.setAttribute("aria-atomic", "true");
        }

        const getCards = () => Array.from(root.querySelectorAll("[data-ssllabs-filter-card]"))
            .filter((card) => card instanceof HTMLElement);

        const getRows = () => Array.from(root.querySelectorAll("[data-ssllabs-site-row]"))
            .filter((row) => row instanceof HTMLTableRowElement);

        const rows = getRows();
        const siteGroups = [];
        for (let index = 0; index < rows.length;) {
            const firstRow = rows[index];
            const siteCell = firstRow.querySelector(".ssllabs-site-cell");
            if (!(siteCell instanceof HTMLTableCellElement)) {
                index += 1;
                continue;
            }
            const groupSize = Math.max(1, siteCell.rowSpan);
            siteGroups.push({ siteCell, rows: rows.slice(index, index + groupSize) });
            index += groupSize;
        }

        const autosaveSelects = Array.from(root.querySelectorAll("[data-ssllabs-autosave]"))
            .filter((el) => el instanceof HTMLSelectElement);
        const handleAutosaveChange = (event) => {
            removeAutoQueuedFlash();
            const form = event.currentTarget.closest("form");
            if (form instanceof HTMLFormElement) {
                form.requestSubmit();
            }
        };

        const applyFilters = () => {
            const cards = getCards();
            const query = normalize(searchInput.value);
            const queryTokens = query.split(" ").filter(Boolean);
            const selectedGrade = normalize(gradeSelect.value);
            let visibleCards = 0;
            const visiblePresetCounts = { all: 0, issues: 0, "not-scanned": 0 };

            for (const card of cards) {
                const haystack = normalize(card.dataset.ssllabsSearch);
                const grade = normalize(card.dataset.ssllabsGrade);
                const matchesQuery = queryTokens.length === 0 || queryTokens.every((token) => haystack.includes(token));
                const matchesGrade = !selectedGrade || grade === selectedGrade;
                const isIssue = ["b", "c", "d", "e", "f", "mixed", "failed"].includes(grade);
                const matchesPreset = activePreset === "all"
                    || (activePreset === "issues" && isIssue)
                    || (activePreset === "not-scanned" && grade === "not-scanned");
                const isVisible = matchesQuery && matchesGrade && matchesPreset;
                card.hidden = !isVisible;
                if (isVisible) {
                    visibleCards += 1;
                }
                if (matchesQuery && matchesGrade) {
                    visiblePresetCounts.all += 1;
                    if (isIssue) {
                        visiblePresetCounts.issues += 1;
                    }
                    if (grade === "not-scanned") {
                        visiblePresetCounts["not-scanned"] += 1;
                    }
                }
            }

            for (const row of rows) {
                const hasVisibleCard = Boolean(row.querySelector("[data-ssllabs-filter-card]:not([hidden])"));
                row.hidden = !hasVisibleCard;
            }

            for (const group of siteGroups) {
                for (const row of group.rows) {
                    row.removeAttribute("data-ssllabs-site-group-end");
                }
                const visibleRows = group.rows.filter((row) => !row.hidden);
                if (visibleRows.length === 0) {
                    continue;
                }
                group.siteCell.rowSpan = visibleRows.length;
                const firstVisibleRow = visibleRows[0];
                if (group.siteCell.parentElement !== firstVisibleRow) {
                    firstVisibleRow.insertBefore(group.siteCell, firstVisibleRow.firstElementChild);
                }
            }

            const visibleGroups = siteGroups.filter((group) => group.rows.some((row) => !row.hidden));
            visibleGroups.forEach((group, index) => {
                if (index === visibleGroups.length - 1) {
                    return;
                }
                const visibleRows = group.rows.filter((row) => !row.hidden);
                const lastVisibleRow = visibleRows[visibleRows.length - 1];
                if (lastVisibleRow) {
                    lastVisibleRow.setAttribute("data-ssllabs-site-group-end", "");
                }
            });

            if (visibleCount instanceof HTMLElement) {
                visibleCount.textContent = String(visibleCards);
                visibleCount.hidden = visibleCards === 0;
            }

            if (visibleLabel instanceof HTMLElement) {
                visibleLabel.textContent = visibleCards === 0 ? "No domains found" : "Domains found";
            }

            if (emptyState instanceof HTMLElement) {
                emptyState.hidden = visibleCards !== 0 || cards.length === 0;
            }

            if (clearFiltersButton instanceof HTMLButtonElement) {
                clearFiltersButton.disabled = query === "" && selectedGrade === "" && activePreset === "all";
            }

            for (const count of presetCounts) {
                const preset = count.dataset.ssllabsPresetCount;
                if (preset && preset in visiblePresetCounts) {
                    count.textContent = String(visiblePresetCounts[preset]);
                }
            }

            for (const button of quickFilterButtons) {
                button.classList.toggle("is-active", button.dataset.ssllabsFilterPreset === activePreset);
            }
        };

        const handleSearchInput = () => applyFilters();
        const handleGradeChange = () => applyFilters();
        const handleClearFilters = () => {
            searchInput.value = "";
            gradeSelect.value = "";
            activePreset = "all";
            applyFilters();
            searchInput.focus({ preventScroll: true });
        };
        const handleQuickFilter = (event) => {
            const button = event.currentTarget;
            if (button instanceof HTMLButtonElement) {
                gradeSelect.value = "";
                activePreset = button.dataset.ssllabsFilterPreset || "all";
                applyFilters();
            }
        };

        searchInput.addEventListener("input", handleSearchInput);
        gradeSelect.addEventListener("change", handleGradeChange);
        for (const select of autosaveSelects) {
            select.addEventListener("change", handleAutosaveChange);
        }
        for (const button of quickFilterButtons) {
            button.addEventListener("click", handleQuickFilter);
        }

        if (clearFiltersButton instanceof HTMLButtonElement) {
            clearFiltersButton.addEventListener("click", handleClearFilters);
        }

        root.dataset.ssllabsFilterInitialized = "true";

        root.ssllabsFilterCleanup = () => {
            searchInput.removeEventListener("input", handleSearchInput);
            gradeSelect.removeEventListener("change", handleGradeChange);
            for (const select of autosaveSelects) {
                select.removeEventListener("change", handleAutosaveChange);
            }
            for (const button of quickFilterButtons) {
                button.removeEventListener("click", handleQuickFilter);
            }
            if (clearFiltersButton instanceof HTMLButtonElement) {
                clearFiltersButton.removeEventListener("click", handleClearFilters);
            }
            delete root.dataset.ssllabsFilterInitialized;
            root.ssllabsFilterCleanup = null;
        };

        applyFilters();
    };

    const App = window.CaddyBuddyApp || (window.CaddyBuddyApp = {});
    App.initializeSslLabsFilter = initializeSslLabsFilter;

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initializeSslLabsFilter, { once: true });
    } else {
        initializeSslLabsFilter();
    }
})();

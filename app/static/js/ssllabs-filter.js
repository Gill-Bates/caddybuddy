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
            .filter((row) => row instanceof HTMLElement);

        const summaryActions = Array.from(root.querySelectorAll(".ssllabs-domain-card__summary button, .ssllabs-domain-card__summary a, .ssllabs-domain-card__summary select"))
            .filter((action) => action instanceof HTMLElement);
        const handleSummaryActionClick = (event) => {
            event.stopPropagation();
        };

        const autosaveSelects = Array.from(root.querySelectorAll("[data-ssllabs-autosave]"))
            .filter((el) => el instanceof HTMLSelectElement);
        const handleAutosaveChange = (event) => {
            const form = event.currentTarget.closest("form");
            if (form instanceof HTMLFormElement) {
                form.requestSubmit();
            }
        };

        const applyFilters = () => {
            const cards = getCards();
            const rows = getRows();
            const query = normalize(searchInput.value);
            const queryTokens = query.split(" ").filter(Boolean);
            const selectedGrade = normalize(gradeSelect.value);
            let visibleCards = 0;

            for (const card of cards) {
                const haystack = normalize(card.dataset.ssllabsSearch);
                const grade = normalize(card.dataset.ssllabsGrade);
                const matchesQuery = queryTokens.length === 0 || queryTokens.every((token) => haystack.includes(token));
                const matchesGrade = !selectedGrade || grade === selectedGrade;
                const isVisible = matchesQuery && matchesGrade;
                card.hidden = !isVisible;
                if (isVisible) {
                    visibleCards += 1;
                }
            }

            for (const row of rows) {
                const hasVisibleCard = Boolean(row.querySelector("[data-ssllabs-filter-card]:not([hidden])"));
                row.hidden = !hasVisibleCard;
            }

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
                clearFiltersButton.disabled = query === "" && selectedGrade === "";
            }
        };

        const handleSearchInput = () => applyFilters();
        const handleGradeChange = () => applyFilters();
        const handleClearFilters = () => {
            searchInput.value = "";
            gradeSelect.value = "";
            applyFilters();
            searchInput.focus({ preventScroll: true });
        };

        searchInput.addEventListener("input", handleSearchInput);
        gradeSelect.addEventListener("change", handleGradeChange);
        for (const action of summaryActions) {
            action.addEventListener("click", handleSummaryActionClick);
        }
        for (const select of autosaveSelects) {
            select.addEventListener("change", handleAutosaveChange);
        }

        if (clearFiltersButton instanceof HTMLButtonElement) {
            clearFiltersButton.addEventListener("click", handleClearFilters);
        }

        root.dataset.ssllabsFilterInitialized = "true";

        root.ssllabsFilterCleanup = () => {
            searchInput.removeEventListener("input", handleSearchInput);
            gradeSelect.removeEventListener("change", handleGradeChange);
            for (const action of summaryActions) {
                action.removeEventListener("click", handleSummaryActionClick);
            }
            for (const select of autosaveSelects) {
                select.removeEventListener("change", handleAutosaveChange);
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

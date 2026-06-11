//
// app/static/js/ssllabs-filter.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

(() => {
    "use strict";

    const root = document.querySelector("[data-ssllabs-filter-root]");
    if (!(root instanceof HTMLElement)) {
        return;
    }
    if (root.dataset.ssllabsFilterInitialized === "true") {
        return;
    }
    root.dataset.ssllabsFilterInitialized = "true";

    const searchInput = root.querySelector("[data-ssllabs-search]");
    const gradeSelect = root.querySelector("[data-ssllabs-grade-filter]");
    const clearFiltersButton = root.querySelector("[data-ssllabs-clear-filters]");
    const visibleCount = root.querySelector("[data-ssllabs-visible-count]");
    const visibleLabel = root.querySelector("[data-ssllabs-visible-label]");
    const emptyState = root.querySelector("[data-ssllabs-empty]");

    const normalize = (value) => String(value ?? "")
        .trim()
        .replace(/\s+/g, " ")
        .toLowerCase();

    const cards = Array.from(root.querySelectorAll("[data-ssllabs-filter-card]"))
        .filter((card) => card instanceof HTMLElement);
    const rows = Array.from(root.querySelectorAll("[data-ssllabs-site-row]"))
        .filter((row) => row instanceof HTMLElement);

    const applyFilters = () => {
        const query = normalize(searchInput instanceof HTMLInputElement ? searchInput.value : "");
        const queryTokens = query.split(" ").filter(Boolean);
        const selectedGrade = normalize(gradeSelect instanceof HTMLSelectElement ? gradeSelect.value : "");
        let visibleCards = 0;

        for (const card of cards) {
            if (!(card instanceof HTMLElement)) {
                continue;
            }
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
            if (!(row instanceof HTMLElement)) {
                continue;
            }
            const hasVisibleCard = Boolean(row.querySelector("[data-ssllabs-filter-card]:not([hidden])"));
            row.hidden = !hasVisibleCard;
        }

        if (visibleCount instanceof HTMLElement) {
            visibleCount.textContent = String(visibleCards);
            visibleCount.hidden = visibleCards === 0;
        }

        if (visibleLabel instanceof HTMLElement) {
            visibleLabel.textContent = visibleCards === 0 ? "No Domains found!" : "Domains found";
        }

        if (emptyState instanceof HTMLElement) {
            emptyState.hidden = visibleCards !== 0 || cards.length === 0;
        }
    };

    if (searchInput instanceof HTMLInputElement) {
        searchInput.addEventListener("input", applyFilters);
    }

    if (gradeSelect instanceof HTMLSelectElement) {
        gradeSelect.addEventListener("change", applyFilters);
    }

    if (clearFiltersButton instanceof HTMLButtonElement) {
        clearFiltersButton.addEventListener("click", () => {
            if (searchInput instanceof HTMLInputElement) {
                searchInput.value = "";
            }
            if (gradeSelect instanceof HTMLSelectElement) {
                gradeSelect.value = "";
            }
            applyFilters();
            if (searchInput instanceof HTMLInputElement) {
                searchInput.focus();
            }
        });
    }

    applyFilters();
})();
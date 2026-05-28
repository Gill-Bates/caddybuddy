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
    const visibleCount = root.querySelector("[data-ssllabs-visible-count]");
    const visibleLabel = root.querySelector("[data-ssllabs-visible-label]");
    const emptyState = root.querySelector("[data-ssllabs-empty]");

    const normalize = (value) => String(value ?? "")
        .trim()
        .replace(/\s+/g, " ")
        .toLowerCase();

    const getCards = () => Array.from(root.querySelectorAll("[data-ssllabs-filter-card]"));
    const getRows = () => Array.from(root.querySelectorAll("[data-ssllabs-site-row]"));

    const applyFilters = () => {
        const query = normalize(searchInput instanceof HTMLInputElement ? searchInput.value : "");
        const queryTokens = query.split(" ").filter(Boolean);
        const selectedGrade = normalize(gradeSelect instanceof HTMLSelectElement ? gradeSelect.value : "");
        const cards = getCards();
        const rows = getRows();
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

    applyFilters();
})();
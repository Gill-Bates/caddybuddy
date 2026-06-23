//
// app/static/js/ssllabs-history-chart.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

// Dashboard SSL Labs history. The overview chart aggregates the fleet: one stacked bar
// per week, one segment per grade, plus an "A+ %" overlay line. Domains are never datasets
// here — they live in the search/drilldown focus chart. A persistent inspector shows the
// selected week's breakdown without requiring hover.

(() => {
    "use strict";

    const App = window.CaddyBuddyApp || (window.CaddyBuddyApp = {});

    // Ranks mirror GRADE_RANKS on the server; ordered best -> worst for stacking.
    const GRADE_ORDER = [7, 6, 5, 4, 3, 2, 1, 0, -1];
    const GRADE_META = {
        7: { label: "A+", color: "#15803d" },
        6: { label: "A", color: "#65a30d" },
        5: { label: "A-", color: "#84cc16" },
        4: { label: "B", color: "#eab308" },
        3: { label: "C", color: "#f97316" },
        2: { label: "D", color: "#ea580c" },
        1: { label: "E", color: "#dc2626" },
        0: { label: "F", color: "#991b1b" },
        "-1": { label: "T/M", color: "#64748b" },
    };

    // Mutable per-fetch: updated from grade_scale in the API payload so labels and rank
    // ordering stay in sync with the server without hard-coding them here.
    let gradeOrder = GRADE_ORDER;
    let gradeMeta = GRADE_META;

    const FOCUS_RANK_MIN = -1;
    const FOCUS_RANK_MAX = 7;
    const MAX_PINNED = 8;
    const DAY_MS = 24 * 60 * 60 * 1000;
    const WEEK_MS = 7 * DAY_MS;
    const FOCUS_PALETTE = [
        "#0f766e", "#6366f1", "#d97706", "#dc2626",
        "#0891b2", "#7c3aed", "#15803d", "#db2777",
    ];

    const buildGradeScale = (payload) => {
        const scale = payload?.grade_scale;
        if (!scale || typeof scale !== "object" || Object.keys(scale).length === 0) {
            return { gradeOrder: [...GRADE_ORDER], gradeMeta: { ...GRADE_META } };
        }

        const newMeta = { ...GRADE_META };
        const ranks = new Set();
        for (const [label, rawRank] of Object.entries(scale)) {
            const rank = Number(rawRank);
            if (!Number.isFinite(rank)) {
                continue;
            }
            ranks.add(rank);
            if (rank === -1) {
                newMeta[rank] = GRADE_META["-1"];
                continue;
            }
            if (!newMeta[rank]) {
                newMeta[rank] = {
                    label,
                    color: "#64748b",
                };
            }
        }

        return {
            gradeOrder: ranks.size > 0 ? [...ranks].sort((a, b) => b - a) : [...GRADE_ORDER],
            gradeMeta: newMeta,
        };
    };

    // Apply server-side grade_scale (label → rank dict) so the chart stays in sync.
    // Falls back to local constants when the payload is absent or malformed.
    const applyGradeScale = (payload) => {
        const nextScale = buildGradeScale(payload);
        gradeOrder = nextScale.gradeOrder;
        gradeMeta = nextScale.gradeMeta;
    };

    const isoWeekStart = (date) => {
        const day = date.getUTCDay() || 7;
        const start = new Date(Date.UTC(
            date.getUTCFullYear(),
            date.getUTCMonth(),
            date.getUTCDate() - day + 1,
        ));
        return start.toISOString().slice(0, 10);
    };

    const weekEndIso = (weekStart) => {
        const end = new Date(`${weekStart}T00:00:00Z`);
        end.setUTCDate(end.getUTCDate() + 6);
        return end.toISOString().slice(0, 10);
    };

    const resolveUrl = (rawUrl) => {
        if (typeof App.resolveSameOriginUrl === "function") {
            return App.resolveSameOriginUrl(rawUrl);
        }
        const url = new URL(rawUrl, window.location.origin);
        if (url.origin !== window.location.origin) {
            throw new Error("External URLs are not allowed.");
        }
        return url.toString();
    };

    const fetchJson = async (url) => {
        const options = { credentials: "same-origin", headers: { Accept: "application/json" } };
        const response = typeof App.fetchWithTimeout === "function"
            ? await App.fetchWithTimeout(resolveUrl(url), options, 12000)
            : await fetch(resolveUrl(url), options);
        if (!response.ok) {
            throw new Error(`Request failed: ${response.status}`);
        }
        return response.json();
    };

    const isAbortLikeError = (error) => {
        const message = String(error?.message || "").toLowerCase();
        return (error instanceof DOMException && error.name === "AbortError")
            || message.includes("abort")
            || message.includes("cancel");
    };

    const isMobile = () => window.matchMedia("(max-width: 767px)").matches;

    const themeColors = () => {
        const isDark = document.documentElement.getAttribute("data-bs-theme") === "dark";
        const root = getComputedStyle(document.documentElement);
        const muted = root.getPropertyValue("--cb-muted").trim() || (isDark ? "#b7c2d0" : "#5c6773");
        return {
            textColor: muted,
            gridColor: isDark ? "rgba(148,163,184,0.16)" : "rgba(21,34,46,0.08)",
        };
    };

    // Inclusive list of ISO week starts spanning `days` days, ending in the current UTC week.
    const buildWeeklyLabels = (days) => {
        const span = Math.max(7, Number(days) || 30);
        const labels = [];
        const today = new Date();
        const end = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate()));
        const first = new Date(end.getTime() - (span - 1) * DAY_MS);
        const endWeek = isoWeekStart(end);
        let cursor = new Date(`${isoWeekStart(first)}T00:00:00Z`);

        while (cursor.toISOString().slice(0, 10) <= endWeek) {
            labels.push(cursor.toISOString().slice(0, 10));
            cursor = new Date(cursor.getTime() + WEEK_MS);
        }
        return labels;
    };

    const formatLabel = (isoDate) => {
        const parsed = new Date(`${isoDate}T00:00:00Z`);
        if (Number.isNaN(parsed.getTime())) {
            return isoDate;
        }
        return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
    };

    const formatFullPeriod = (isoDate) => {
        const parsed = new Date(`${isoDate}T00:00:00Z`);
        if (Number.isNaN(parsed.getTime())) {
            return isoDate;
        }
        return `Week of ${parsed.toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric", timeZone: "UTC" })}`;
    };

    // Forward-fill the last known grade per host to each week's end. Returns an
    // array (one entry per week) of Map<host, {rank, grade}>.
    const buildWeeklyState = (series, weekLabels) => {
        const stateByWeek = weekLabels.map(() => new Map());
        for (const entry of series) {
            const points = [...(entry.points || [])].sort((a, b) => a.date.localeCompare(b.date));
            let pointIndex = 0;
            let current = null;
            weekLabels.forEach((weekStart, weekIndex) => {
                const weekEnd = weekEndIso(weekStart);
                while (pointIndex < points.length && points[pointIndex].date <= weekEnd) {
                    current = points[pointIndex];
                    pointIndex += 1;
                }
                if (current) {
                    stateByWeek[weekIndex].set(entry.host, { rank: current.rank, grade: current.grade });
                }
            });
        }
        return stateByWeek;
    };

    const buildDistribution = (stateByPeriod) => {
        const distribution = Object.fromEntries(gradeOrder.map((rank) => [rank, []]));
        const aPlusRate = [];
        stateByPeriod.forEach((hosts) => {
            const counts = Object.fromEntries(gradeOrder.map((rank) => [rank, 0]));
            for (const value of hosts.values()) {
                if (counts[value.rank] !== undefined) {
                    counts[value.rank] += 1;
                }
            }
            const total = hosts.size || 0;
            gradeOrder.forEach((rank) => distribution[rank].push(counts[rank]));
            aPlusRate.push(total ? Math.round((counts[7] / total) * 1000) / 10 : null);
        });
        return { distribution, aPlusRate };
    };

    const buildDistributionDatasets = (distribution, aPlusRate, showRate) => [
        ...gradeOrder.map((rank) => ({
            type: "bar",
            label: gradeMeta[rank].label,
            data: distribution[rank],
            backgroundColor: gradeMeta[rank].color,
            borderWidth: 0,
            stack: "grades",
            order: 2,
            _rank: rank,
        })),
        ...(showRate ? [
        {
            type: "line",
            label: "A+ %",
            data: aPlusRate,
            yAxisID: "rate",
            borderColor: "#0ea5e9",
            backgroundColor: "#0ea5e9",
            borderWidth: 2,
            pointRadius: 3,
            pointHoverRadius: 5,
            tension: 0,
            spanGaps: true,
            order: 1,
        },
        ] : []),
    ];

    const withAlpha = (hex, alpha) => {
        const value = hex.replace("#", "");
        if (value.length !== 6) {
            return hex;
        }
        const r = parseInt(value.slice(0, 2), 16);
        const g = parseInt(value.slice(2, 4), 16);
        const b = parseInt(value.slice(4, 6), 16);
        return `rgba(${r}, ${g}, ${b}, ${alpha})`;
    };

    App.initializeSslLabsHistoryChart = () => {
        const container = document.querySelector("[data-ssllabs-history]");
        if (!container || typeof window.Chart === "undefined") {
            return;
        }

        const baseUrl = container.getAttribute("data-ssllabs-history-url");
        const canvas = container.querySelector("#ssllabs-history-chart");
        if (!baseUrl || !(canvas instanceof HTMLCanvasElement)) {
            return;
        }

        // Mark initialized only after we verified the minimum required structure.
        if (typeof App.markInitialized === "function"
            && !App.markInitialized(container, "ssllabsHistoryInitialized")) {
            return;
        }

        const rangeSelect = container.querySelector("#ssllabs-history-range");
        const emptyEl = container.querySelector("#ssllabs-history-empty");
        const legendEl = container.querySelector("#ssllabs-grade-legend");
        const inspectorEl = container.querySelector("#ssllabs-history-inspector");
        const searchInput = container.querySelector("#ssllabs-domain-search");
        const searchWrap = searchInput?.closest(".ssllabs-domain-search");
        const domainOptions = container.querySelector("#ssllabs-domain-options");
        const problemEl = container.querySelector("#ssllabs-problem-domains");
        const focusWrap = container.querySelector("#ssllabs-focus-wrap");
        const pinnedEl = container.querySelector("#ssllabs-pinned-domains");
        const focusCanvas = container.querySelector("#ssllabs-domain-focus-chart");
        const periodList = container.querySelector("#ssllabs-history-periods");

        let chart = null;
        let focusChart = null;
        let stateByPeriod = [];
        let isoLabels = [];
        let seriesByHost = new Map();
        let selectedIndex = -1;
        const pinned = new Set();
        const setLoading = (loading) => {
            container.classList.toggle("ssllabs-history-card--loading", loading);
        };

        const showEmpty = (show) => {
            container.classList.toggle("is-empty", show);
            if (emptyEl) {
                emptyEl.classList.toggle("d-none", !show);
            }
            canvas.classList.toggle("d-none", show);
        };

        const setHistoryUiVisible = (visible) => {
            legendEl?.classList.toggle("d-none", !visible);
            searchWrap?.classList.toggle("d-none", !visible);
            inspectorEl?.classList.toggle("d-none", !visible);
            problemEl?.classList.toggle("d-none", !visible);
            periodList?.classList.toggle("d-none", !visible);
            if (!visible) {
                focusWrap?.classList.add("d-none");
            }
        };

        const renderLegend = () => {
            if (!legendEl) {
                return;
            }
            legendEl.replaceChildren();
            for (const rank of gradeOrder) {
                const meta = gradeMeta[rank];
                if (!meta) {
                    continue;
                }
                const item = document.createElement("span");
                item.className = "ssllabs-grade-legend__item";
                const swatch = document.createElement("span");
                swatch.className = "ssllabs-grade-legend__swatch";
                swatch.dataset.gradeRank = String(rank);
                const text = document.createElement("span");
                text.textContent = meta.label;
                item.append(swatch, text);
                legendEl.append(item);
            }
        };

        const renderInspector = (index) => {
            if (!inspectorEl) {
                return;
            }
            if (index < 0 || index >= stateByPeriod.length) {
                inspectorEl.replaceChildren();
                return;
            }
            const hosts = stateByPeriod[index];
            const counts = Object.fromEntries(gradeOrder.map((rank) => [rank, 0]));
            for (const value of hosts.values()) {
                if (counts[value.rank] !== undefined) {
                    counts[value.rank] += 1;
                }
            }
            const total = hosts.size;
            const notAPlus = total - counts[7];

            let improved = 0;
            let worsened = 0;
            if (index > 0) {
                const prev = stateByPeriod[index - 1];
                for (const [host, value] of hosts.entries()) {
                    const before = prev.get(host);
                    if (before) {
                        if (value.rank > before.rank) {
                            improved += 1;
                        } else if (value.rank < before.rank) {
                            worsened += 1;
                        }
                    }
                }
            }

            const heading = document.createElement("div");
            heading.className = "ssllabs-history-inspector__date";
            heading.textContent = formatFullPeriod(isoLabels[index]);

            const list = document.createElement("dl");
            list.className = "ssllabs-history-inspector__grades";
            for (const rank of gradeOrder) {
                if (counts[rank] === 0) {
                    continue;
                }
                const meta = gradeMeta[rank];
                const term = document.createElement("dt");
                const swatch = document.createElement("span");
                swatch.className = "ssllabs-grade-legend__swatch";
                swatch.dataset.gradeRank = String(rank);
                term.append(swatch, document.createTextNode(meta ? meta.label : String(rank)));
                const value = document.createElement("dd");
                value.textContent = String(counts[rank]);
                list.append(term, value);
            }

            const summary = document.createElement("ul");
            summary.className = "ssllabs-history-inspector__summary";
            const rows = [
                ["Domains", String(total)],
                ["Not A+", String(notAPlus)],
            ];
            if (index > 0) {
                rows.push(["Improved vs. previous week", String(improved)]);
                rows.push(["Worsened vs. previous week", String(worsened)]);
            }
            for (const [label, value] of rows) {
                const li = document.createElement("li");
                const name = document.createElement("span");
                name.textContent = label;
                const num = document.createElement("strong");
                num.textContent = value;
                li.append(name, num);
                summary.append(li);
            }

            inspectorEl.replaceChildren(heading, list, summary);
        };

        const selectDate = (index) => {
            selectedIndex = index;
            renderInspector(index);
            renderPeriodButtons();
        };

        const currentNonAPlusHosts = () => {
            if (selectedIndex < 0 || selectedIndex >= stateByPeriod.length) {
                return [];
            }
            const hosts = stateByPeriod[selectedIndex];
            const result = [];
            for (const [host, value] of hosts.entries()) {
                if (value.rank !== 7) {
                    result.push({ host, grade: value.grade, rank: value.rank });
                }
            }
            result.sort((a, b) => a.rank - b.rank || a.host.localeCompare(b.host));
            return result;
        };

        const renderProblemDomains = () => {
            if (!problemEl) {
                return;
            }
            problemEl.replaceChildren();
            // No data at all -> render nothing (the chart's own empty-state covers this).
            const hasPeriod = selectedIndex >= 0 && selectedIndex < stateByPeriod.length
                && stateByPeriod[selectedIndex].size > 0;
            if (!hasPeriod) {
                return;
            }
            const problems = currentNonAPlusHosts();
            if (problems.length === 0) {
                const ok = document.createElement("p");
                ok.className = "ssllabs-problem-domains__empty";
                ok.textContent = "All domains at A+ in the selected week.";
                problemEl.append(ok);
                return;
            }
            const heading = document.createElement("div");
            heading.className = "ssllabs-problem-domains__heading";
            heading.textContent = `Currently not A+ (${problems.length})`;
            const list = document.createElement("div");
            list.className = "ssllabs-problem-domains__list";
            for (const item of problems) {
                const btn = document.createElement("button");
                btn.type = "button";
                btn.className = "ssllabs-problem-domains__item";
                btn.setAttribute("aria-pressed", pinned.has(item.host) ? "true" : "false");
                const grade = document.createElement("span");
                grade.className = "ssllabs-problem-domains__grade";
                grade.dataset.gradeRank = String(item.rank);
                grade.textContent = item.grade || "?";
                const host = document.createElement("span");
                host.textContent = item.host;
                btn.append(grade, host);
                btn.addEventListener("click", () => togglePin(item.host));
                list.append(btn);
            }
            problemEl.append(heading, list);
        };

        const renderDomainOptions = () => {
            if (!domainOptions) {
                return;
            }
            domainOptions.replaceChildren();
            for (const host of [...seriesByHost.keys()].sort()) {
                const option = document.createElement("option");
                option.value = host;
                domainOptions.append(option);
            }
        };

        const renderPeriodButtons = () => {
            if (!periodList) {
                return;
            }
            periodList.replaceChildren();
            isoLabels.forEach((label, index) => {
                const button = document.createElement("button");
                button.type = "button";
                button.className = "ssllabs-history-period";
                button.textContent = formatLabel(label);
                button.setAttribute("aria-pressed", index === selectedIndex ? "true" : "false");
                button.addEventListener("click", () => {
                    selectDate(index);
                    renderProblemDomains();
                });
                periodList.append(button);
            });
        };

        const renderPinnedChips = () => {
            if (!pinnedEl) {
                return;
            }
            pinnedEl.replaceChildren();
            for (const host of pinned) {
                const chip = document.createElement("button");
                chip.type = "button";
                chip.className = "ssllabs-pinned-domains__chip";
                chip.setAttribute("aria-label", `Unpin ${host}`);
                chip.append(document.createTextNode(host), document.createTextNode(" ×"));
                chip.addEventListener("click", () => togglePin(host));
                pinnedEl.append(chip);
            }
        };

        const buildFocusDatasets = () => {
            let colorIndex = 0;
            const datasets = [];
            for (const host of pinned) {
                const entry = seriesByHost.get(host);
                if (!entry) {
                    continue;
                }
                const color = FOCUS_PALETTE[colorIndex % FOCUS_PALETTE.length];
                colorIndex += 1;
                const data = new Array(isoLabels.length).fill(null);
                const grades = new Array(isoLabels.length).fill(null);
                // Forward-fill to weekly buckets so the stepped line spans gaps between sparse scans.
                const points = [...(entry.points || [])].sort((a, b) => a.date.localeCompare(b.date));
                let pointIndex = 0;
                let current = null;
                isoLabels.forEach((weekStart, dateIndex) => {
                    const weekEnd = weekEndIso(weekStart);
                    while (pointIndex < points.length && points[pointIndex].date <= weekEnd) {
                        current = points[pointIndex];
                        pointIndex += 1;
                    }
                    if (current) {
                        data[dateIndex] = current.rank;
                        grades[dateIndex] = current.grade;
                    }
                });
                datasets.push({
                    label: host,
                    data,
                    _grades: grades,
                    borderColor: color,
                    backgroundColor: withAlpha(color, 0.12),
                    fill: false,
                    stepped: true,
                    spanGaps: true,
                    tension: 0,
                    pointRadius: 2,
                    pointHoverRadius: 4,
                });
            }
            return datasets;
        };

        const renderFocusChart = () => {
            if (!focusWrap || !focusCanvas) {
                return;
            }
            renderPinnedChips();
            if (pinned.size === 0) {
                focusWrap.classList.add("d-none");
                if (focusChart) {
                    focusChart.destroy();
                    focusChart = null;
                }
                return;
            }
            focusWrap.classList.remove("d-none");
            const colors = themeColors();
            const mobile = isMobile();
            const datasets = buildFocusDatasets();
            if (!focusChart) {
                focusChart = new window.Chart(focusCanvas, {
                    type: "line",
                    data: { labels: isoLabels.map(formatLabel), datasets },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        animation: false,
                        interaction: { mode: "index", intersect: false },
                        plugins: {
                            legend: {
                                position: "top",
                                labels: { color: colors.textColor, usePointStyle: true, boxWidth: 8, font: { size: mobile ? 11 : 12 } },
                            },
                            tooltip: {
                                callbacks: {
                                    label: (ctx) => `${ctx.dataset.label}: ${ctx.dataset?._grades?.[ctx.dataIndex] || "–"}`,
                                },
                            },
                        },
                        scales: {
                            x: { ticks: { color: colors.textColor, maxTicksLimit: mobile ? 6 : 12, autoSkip: true, maxRotation: 0 }, grid: { color: colors.gridColor } },
                            y: {
                                min: FOCUS_RANK_MIN - 0.5,
                                max: FOCUS_RANK_MAX + 0.5,
                                ticks: { color: colors.textColor, stepSize: 1, callback: (value) => gradeMeta[String(value)]?.label ?? "" },
                                grid: { color: colors.gridColor },
                            },
                        },
                    },
                });
            } else {
                focusChart.data.labels = isoLabels.map(formatLabel);
                focusChart.data.datasets = datasets;
                focusChart.update("none");
            }
        };

        // External tooltip handler for the main chart. Chart.js calls this with opacity=0
        // when the cursor leaves the chart area; we revert to the pinned selected week then.
        const renderExternalInspectorTooltip = (context) => {
            const tooltip = context.tooltip;
            if (!tooltip || tooltip.opacity === 0) {
                if (selectedIndex >= 0) {
                    renderInspector(selectedIndex);
                }
                return;
            }
            const dataPoints = tooltip.dataPoints;
            if (dataPoints && dataPoints.length > 0) {
                renderInspector(dataPoints[0].dataIndex);
            }
        };

        const togglePin = (host) => {
            if (pinned.has(host)) {
                pinned.delete(host);
                renderProblemDomains();
                renderFocusChart();
                return;
            }
            if (pinned.size >= MAX_PINNED) {
                if (pinnedEl) {
                    const msg = document.createElement("span");
                    msg.className = "ssllabs-pinned-domains__limit";
                    msg.setAttribute("role", "status");
                    msg.textContent = `Maximum of ${MAX_PINNED} domains pinned.`;
                    pinnedEl.append(msg);
                    setTimeout(() => msg.remove(), 3000);
                }
                return;
            }
            pinned.add(host);
            renderProblemDomains();
            renderFocusChart();
        };

        const buildChart = () => {
            const colors = themeColors();
            const mobile = isMobile();
            chart = new window.Chart(canvas, {
                type: "bar",
                data: { labels: [], datasets: [] },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    normalized: true,
                    interaction: { mode: "index", axis: "x", intersect: false },
                    onClick: (_event, elements) => {
                        if (elements.length) {
                            selectDate(elements[0].index);
                            renderProblemDomains();
                        }
                    },
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            enabled: false,
                            external: renderExternalInspectorTooltip,
                        },
                    },
                    scales: {
                        x: { stacked: true, grid: { display: false }, ticks: { color: colors.textColor, maxRotation: 0, autoSkip: true, maxTicksLimit: mobile ? 6 : 14 } },
                        y: {
                            stacked: true,
                            beginAtZero: true,
                            title: { display: !mobile, text: "Domains", color: colors.textColor },
                            ticks: { color: colors.textColor, precision: 0 },
                            grid: { color: colors.gridColor },
                        },
                        rate: {
                            position: "right",
                            min: 0,
                            max: 100,
                            grid: { drawOnChartArea: false },
                            ticks: { color: colors.textColor, callback: (value) => `${value}%` },
                            title: { display: !mobile, text: "A+ %", color: colors.textColor },
                        },
                    },
                },
            });
        };

        const refresh = async () => {
            setLoading(true);
            const range = rangeSelect?.value || "30d";
            let payload;
            try {
                payload = await fetchJson(`${baseUrl}?range_key=${encodeURIComponent(range)}`);
            } catch (error) {
                if (!isAbortLikeError(error)) {
                    console.error("Failed to load SSL Labs history:", error);
                }
                setHistoryUiVisible(false);
                showEmpty(true);
                setLoading(false);
                return;
            }

            applyGradeScale(payload);
            renderLegend();

            const series = Array.isArray(payload?.series) ? payload.series : [];
            const hasData = series.some((entry) => (entry.points || []).length > 0);
            if (!hasData) {
                if (chart) {
                    chart.data.labels = [];
                    chart.data.datasets = [];
                    chart.update("none");
                }
                seriesByHost = new Map();
                stateByPeriod = [];
                isoLabels = [];
                selectedIndex = -1;
                renderInspector(-1);
                renderProblemDomains();
                renderDomainOptions();
                renderPeriodButtons();
                setHistoryUiVisible(false);
                showEmpty(true);
                setLoading(false);
                return;
            }

            setHistoryUiVisible(true);
            showEmpty(false);
            isoLabels = buildWeeklyLabels(payload.days);
            seriesByHost = new Map(series.map((entry) => [entry.host, entry]));
            stateByPeriod = buildWeeklyState(series, isoLabels);
            const { distribution, aPlusRate } = buildDistribution(stateByPeriod);
            const shouldShowAPlusRate = stateByPeriod.some((hosts) => hosts.size >= 5);

            if (!chart) {
                buildChart();
            }
            if (!chart) {
                return;
            }
            chart.data.labels = isoLabels.map(formatLabel);
            chart.data.datasets = buildDistributionDatasets(distribution, aPlusRate, shouldShowAPlusRate);
            chart.options.scales.rate.display = shouldShowAPlusRate;
            chart.update("none");

            // Drop pins whose host disappeared from the new range.
            for (const host of [...pinned]) {
                if (!seriesByHost.has(host)) {
                    pinned.delete(host);
                }
            }

            selectDate(isoLabels.length - 1);
            renderProblemDomains();
            renderDomainOptions();
            renderPeriodButtons();
            renderFocusChart();
            setLoading(false);
        };

        setHistoryUiVisible(false);
        showEmpty(false);
        renderLegend();

        if (rangeSelect) {
            rangeSelect.addEventListener("change", () => {
                refresh().catch((error) => console.error(error));
            });
        }

        if (searchInput instanceof HTMLInputElement) {
            const pinFromSearch = () => {
                const host = searchInput.value.trim();
                if (host && seriesByHost.has(host)) {
                    togglePin(host);
                    searchInput.value = "";
                }
            };
            searchInput.addEventListener("change", pinFromSearch);
            searchInput.addEventListener("keydown", (event) => {
                if (event.key === "Enter") {
                    event.preventDefault();
                    pinFromSearch();
                }
            });
        }

        const cleanup = () => {
            if (chart) {
                chart.destroy();
                chart = null;
            }
            if (focusChart) {
                focusChart.destroy();
                focusChart = null;
            }
        };
        window.addEventListener("beforeunload", cleanup, { once: true });

        refresh().catch((error) => console.error(error));
    };

    if (App.exposeTestHooks === true) {
        App.__testHooks = App.__testHooks || {};
        App.__testHooks.ssllabsHistoryChart = {
            buildGradeScale,
            buildWeeklyLabels,
            buildWeeklyState,
            weekEndIso,
        };
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => App.initializeSslLabsHistoryChart());
    } else {
        App.initializeSslLabsHistoryChart();
    }
})();

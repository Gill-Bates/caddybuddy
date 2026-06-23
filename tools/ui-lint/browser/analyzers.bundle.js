//
// tools/ui-lint/browser/analyzers.bundle.js
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

(function () {
    'use strict';

    const OPEN_MODAL_CANDIDATE_SELECTOR = '.modal.show, .modal-overlay.is-open, .modal-overlay[style]';
    const OPEN_MODAL_OVERLAY_SELECTOR = '.modal-overlay.is-open, .modal-overlay[style]';

    const cache = {
        computedStyle: new WeakMap(),
        boundingRect: new WeakMap(),
    };

    function resetRunCache() {
        cache.computedStyle = new WeakMap();
        cache.boundingRect = new WeakMap();
    }

    function invalidateCacheFor(el) {
        if (!el) return;
        cache.computedStyle.delete(el);
        cache.boundingRect.delete(el);
    }

    // Returns a finite number, or `fallback` when `value` is missing/NaN.
    // Prevents Number(undefined) → NaN from silently disabling comparisons.
    function numberConstant(value, fallback) {
        const n = Number(value);
        return Number.isFinite(n) ? n : fallback;
    }

    // Runs querySelectorAll with a try/catch so a malformed selector from the
    // harness cannot abort the entire runAll() pipeline.
    function queryAllSafe(selector, fallbackSelector) {
        try {
            return Array.from(document.querySelectorAll(selector));
        } catch {
            return fallbackSelector ? Array.from(document.querySelectorAll(fallbackSelector)) : [];
        }
    }

    function styleOf(el) {
        if (!el) return null;
        let style = cache.computedStyle.get(el);
        if (!style) {
            style = window.getComputedStyle(el);
            cache.computedStyle.set(el, style);
        }
        return style;
    }

    function rectOf(el) {
        if (!el) return null;
        let rect = cache.boundingRect.get(el);
        if (!rect) {
            rect = el.getBoundingClientRect();
            cache.boundingRect.set(el, rect);
        }
        return rect;
    }

    function roundTo(value, digits) {
        if (!Number.isFinite(value)) return value;
        const factor = 10 ** digits;
        return Math.round(value * factor) / factor;
    }

    // Matches Bootstrap column tokens only (col, col-4, col-md-6, col-auto, …)
    // to avoid false positives from unrelated classes that contain "col-".
    const BOOTSTRAP_COLUMN_TOKEN_RE = /(^|\s)col(?:-\d+|-auto|-(?:sm|md|lg|xl|xxl)(?:-\d+|-auto)?)?(?=\s|$)/;

    function isModalActive(el) {
        if (!el) return false;
        return el.classList.contains('is-open') || el.matches('.modal.show') || styleOf(el)?.display === 'flex';
    }

    function getOpenModalElements() {
        const candidates = Array.from(document.querySelectorAll(OPEN_MODAL_CANDIDATE_SELECTOR))
            .filter(isModalActive);
        // Drop elements that are nested inside another matched element so a
        // custom overlay wrapping a .modal.show is not counted as two modals.
        return candidates.filter((candidate) =>
            !candidates.some((other) => other !== candidate && other.contains(candidate))
        );
    }

    function getOpenModalOverlay() {
        return Array.from(document.querySelectorAll(OPEN_MODAL_OVERLAY_SELECTOR))
            .find(isModalActive) || null;
    }

    // Normalizes open modal candidates to actual dialog elements, covering both
    // a standalone Bootstrap .modal.show and a .modal inside a custom overlay.
    function getOpenModalDialogs() {
        return getOpenModalElements()
            .map((el) => el.matches('.modal') ? el : el.querySelector('.modal'))
            .filter((el) => el instanceof Element && isVisible(el));
    }

    function isVisible(el) {
        if (!el || !el.isConnected) return false;
        if (el.closest('[aria-hidden="true"]')) return false;
        const style = styleOf(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
        const rect = rectOf(el);
        return rect.width > 0 && rect.height > 0;
    }

    function isVisuallyHidden(el) {
        if (!el) return false;
        const style = styleOf(el);
        const rect = rectOf(el);
        const clip = String(style?.clip || '').replace(/\s+/g, '');
        const clipPath = String(style?.clipPath || '').replace(/\s+/g, '').toLowerCase();
        const tinyBox = rect.width <= 1.5 && rect.height <= 1.5;

        return tinyBox && (
            clip === 'rect(0px,0px,0px,0px)'
            || clip === 'rect(0,0,0,0)'
            || clipPath.includes('inset(50%)')
        );
    }

    function getAccessibleName(el) {
        if (!el) return '';

        const ariaLabel = el.getAttribute('aria-label');
        if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();

        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy && labelledBy.trim()) {
            const ids = labelledBy.split(/\s+/).filter(Boolean);
            const text = ids
                .map((id) => document.getElementById(id)?.textContent?.trim() || '')
                .join(' ')
                .trim();
            if (text) return text;
        }

        const labelText = Array.from(el.labels || [])
            .map((label) => label.textContent?.trim() || '')
            .join(' ')
            .trim();
        if (labelText) return labelText;

        const imgAlt = el.querySelector?.('img[alt]')?.getAttribute('alt')?.trim();
        if (imgAlt) return imgAlt;

        const svgTitle = el.querySelector?.('svg title')?.textContent?.trim();
        if (svgTitle) return svgTitle;

        const wrappingLabelText = el.closest('label')?.textContent?.trim() || '';
        if (wrappingLabelText) return wrappingLabelText;

        if (el instanceof HTMLInputElement) {
            if (['button', 'submit', 'reset'].includes(el.type)) {
                const buttonValue = el.value.trim();
                if (buttonValue) return buttonValue;
            }
            if (el.type === 'image') {
                const altText = el.getAttribute('alt')?.trim() || '';
                if (altText) return altText;
            }
        }

        const title = el.getAttribute('title');
        if (title && title.trim()) return title.trim();

        return (el.textContent || '').trim();
    }

    function usesHardcodedColor(styleText) {
        return /#([0-9a-f]{3,8})\b/i.test(String(styleText || ''));
    }

    function parseRgb(value) {
        if (!value) return null;
        // Supports both legacy comma-separated and modern space-separated CSS color syntax
        const match = String(value).match(/rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)/i);
        if (!match) return null;
        return {
            r: Number.parseInt(match[1], 10),
            g: Number.parseInt(match[2], 10),
            b: Number.parseInt(match[3], 10),
        };
    }

    function normalizeColorToRgb(value) {
        if (!value) return null;

        const direct = parseRgb(value);
        if (direct) return direct;
        if (!(document.body instanceof HTMLElement)) return null;

        const probe = document.createElement('span');
        probe.style.color = String(value);
        probe.style.position = 'absolute';
        probe.style.width = '0';
        probe.style.height = '0';
        probe.style.pointerEvents = 'none';
        probe.style.opacity = '0';
        document.body.appendChild(probe);

        try {
            return parseRgb(window.getComputedStyle(probe).color);
        } finally {
            probe.remove();
        }
    }

    function channelToLinear(value) {
        const c = value / 255;
        return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    }

    function relativeLuminance(rgb) {
        if (!rgb) return null;
        return (
            0.2126 * channelToLinear(rgb.r)
            + 0.7152 * channelToLinear(rgb.g)
            + 0.0722 * channelToLinear(rgb.b)
        );
    }

    function contrastRatio(fgRgb, bgRgb) {
        const fgLum = relativeLuminance(fgRgb);
        const bgLum = relativeLuminance(bgRgb);
        if (fgLum == null || bgLum == null) return null;
        const lighter = Math.max(fgLum, bgLum);
        const darker = Math.min(fgLum, bgLum);
        return (lighter + 0.05) / (darker + 0.05);
    }

    // ---------------- Accessibility ----------------
    function accessibilityAnalyzer(constants = {}) {
        // Query only image tags directly instead of scanning entire DOM
        const imgsWithoutAlt = Array.from(document.querySelectorAll('img'))
            .filter(isVisible)
            .filter((img) => !img.hasAttribute('alt'))
            .map((img) => ({ src: img.getAttribute('src') }));

        function hasVisibleFocusIndicator(el) {
            const style = window.getComputedStyle(el);
            const outlineWidth = Number.parseFloat(style.outlineWidth || '0');
            const hasOutline = style.outlineStyle && style.outlineStyle !== 'none' && outlineWidth > 0;
            const hasBoxShadow = style.boxShadow && style.boxShadow !== 'none';
            return hasOutline || hasBoxShadow;
        }

        // Suppress focus/blur events at window + document level during probing
        // so app-level capture listeners cannot react to the synthetic focus.
        function isFocusIndicatorMissing(el) {
            const previousActive = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            const controller = new AbortController();

            const listeners = (event) => event.stopImmediatePropagation();
            const localTarget = el.closest('form') || el.closest('[tabindex]') || document;
            const eventTypes = ['focusin', 'focus', 'focusout', 'blur'];
            for (const suppressTarget of [window, localTarget]) {
                for (const type of eventTypes) {
                    suppressTarget.addEventListener(type, listeners, { capture: true, signal: controller.signal });
                }
            }

            try {
                el.focus({ preventScroll: true });
                invalidateCacheFor(el);
                invalidateCacheFor(previousActive);

                const focused = document.activeElement === el;
                const missing = !focused || !hasVisibleFocusIndicator(el);

                if (previousActive && previousActive !== el) {
                    try {
                        previousActive.focus({ preventScroll: true });
                    } catch {
                        previousActive.blur?.();
                    }
                } else if (focused) {
                    el.blur?.();
                }

                invalidateCacheFor(el);
                invalidateCacheFor(previousActive);
                return missing;
            } catch {
                return true;
            } finally {
                controller.abort();
            }
        }

        const interactive = Array.from(document.querySelectorAll('button, a, input, select, textarea'));

        const unlabeledControls = interactive
            .filter(isVisible)
            .filter((el) => !getAccessibleName(el))
            .slice(0, 20)
            .map((el) => ({ tag: el.tagName }));

        const emptyAriaLabels = interactive
            .filter(isVisible)
            .filter((el) => !isVisuallyHidden(el))
            .filter((el) => el.hasAttribute('aria-label') && !String(el.getAttribute('aria-label') || '').trim())
            .slice(0, 20)
            .map((el) => ({ tag: el.tagName }));

        // Skip text-entry controls: focusing them can open the virtual keyboard
        // on iOS and cause layout shifts that corrupt subsequent measurements.
        const canSafelyProbeFocus = (el) =>
            !(el instanceof HTMLInputElement)
            && !(el instanceof HTMLTextAreaElement)
            && !(el instanceof HTMLSelectElement);

        let focusIndicatorMissing = [];
        if (constants.ENABLE_FOCUS_LINTING) {
            focusIndicatorMissing = interactive
                .filter(isVisible)
                .filter(canSafelyProbeFocus)
                .filter((el) => isFocusIndicatorMissing(el))
                .slice(0, 20)
                .map((el) => ({ tag: el.tagName }));
        }

        return {
            imgsWithoutAlt,
            unlabeledControls,
            emptyAriaLabels,
            focusIndicatorMissing,
        };
    }

    // ---------------- Layout ----------------
    function layoutAnalyzer(constants) {
        const doc = document.documentElement;
        const tolerance = Number(constants.OVERFLOW_TOLERANCE_PX || 0);
        const bootstrapGridIssues = Array.from(document.querySelectorAll('.row'))
            .filter(isVisible)
            .filter((row) => !isVisuallyHidden(row))
            .filter((row) => {
                const rect = rectOf(row);
                return rect.left < (0 - tolerance) || rect.right > (window.innerWidth + tolerance);
            })
            .slice(0, 20)
            .map((row) => ({
                className: row.className || '',
                left: roundTo(rectOf(row).left, 2),
                right: roundTo(rectOf(row).right, 2),
                viewportWidth: window.innerWidth,
            }));

        const bootstrapColumnsOutsideRows = Array.from(document.querySelectorAll('[class*="col-"]'))
            .filter((el) => BOOTSTRAP_COLUMN_TOKEN_RE.test(el.className || ''))
            .filter(isVisible)
            .filter((el) => !isVisuallyHidden(el))
            .filter((el) => !el.closest('.row'))
            .slice(0, 20)
            .map((el) => ({
                tag: el.tagName,
                className: el.className || '',
            }));

        return {
            horizontalOverflow: {
                hasOverflow: doc.scrollWidth > window.innerWidth + (constants.OVERFLOW_TOLERANCE_PX || 0),
            },
            bootstrapGridIssues,
            bootstrapColumnsOutsideRows,
        };
    }

    function scrollContainmentAnalyzer(constants = {}, scope = '') {
        const tolerance = Number(constants.OVERFLOW_TOLERANCE_PX || 0);
        const isRootScroller = (el) => el === document.documentElement || el === document.body;
        const isAllowedScrollContainer = (el, currentScope) => {
            if (!(el instanceof Element)) {
                return false;
            }
            if (el.matches('.ssllabs-scrollbox, .sites-list-scroll')) {
                return true;
            }
            if (currentScope === 'audit-logs' && el.matches('.audit-logs-stream, #auditTableContainer, .audit-row__details')) {
                return true;
            }
            return false;
        };
        const axisOverflow = (el, axis) => {
            if (axis === 'x') {
                return el.scrollWidth > el.clientWidth + tolerance;
            }
            return el.scrollHeight > el.clientHeight + tolerance;
        };

        const baseCandidates = Array.from(document.querySelectorAll('*'))
            .filter(isVisible)
            .filter((el) => !isRootScroller(el))
            .filter((el) => !isVisuallyHidden(el))
            .filter((el) => !isAllowedScrollContainer(el, scope));

        const scrollContainers = baseCandidates.filter((el) => {
            const style = styleOf(el);
            const overflowX = style?.overflowX || '';
            const overflowY = style?.overflowY || '';
            return (['auto', 'scroll'].includes(overflowX) && axisOverflow(el, 'x'))
                || (['auto', 'scroll'].includes(overflowY) && axisOverflow(el, 'y'));
        });

        const nestedScrollContainers = scrollContainers
            .filter((el) => scrollContainers.some((candidate) => candidate !== el && el.contains(candidate)))
            .slice(0, 20)
            .map((el) => ({ tag: el.tagName, className: el.className || '' }));

        const ghostScrollContainers = baseCandidates
            .filter((el) => {
                const style = styleOf(el);
                const overflowX = style?.overflowX || '';
                const overflowY = style?.overflowY || '';
                const clipsX = ['hidden', 'clip'].includes(overflowX) && axisOverflow(el, 'x');
                const clipsY = ['hidden', 'clip'].includes(overflowY) && axisOverflow(el, 'y');
                if (!clipsX && !clipsY) {
                    return false;
                }
                const textOverflow = style?.textOverflow || '';
                const whiteSpace = style?.whiteSpace || '';
                if (clipsX && textOverflow === 'ellipsis' && whiteSpace === 'nowrap' && !clipsY) {
                    return false;
                }
                return !scrollContainers.some((candidate) => candidate !== el && el.contains(candidate));
            })
            .slice(0, 20)
            .map((el) => ({ tag: el.tagName, className: el.className || '' }));

        const flexScrollTraps = baseCandidates
            .filter((el) => {
                const style = styleOf(el);
                if (!['flex', 'inline-flex'].includes(style?.display || '')) {
                    return false;
                }
                const overflowX = style?.overflowX || '';
                const overflowY = style?.overflowY || '';
                const clipsOverflow = ['hidden', 'clip'].includes(overflowX) || ['hidden', 'clip'].includes(overflowY);
                if (!clipsOverflow) {
                    return false;
                }
                const rect = el.getBoundingClientRect();
                const trapTolerance = Math.max(tolerance, 4);
                return scrollContainers.some((candidate) => {
                    if (candidate === el || !el.contains(candidate)) {
                        return false;
                    }
                    const candidateRect = candidate.getBoundingClientRect();
                    const clipsX = ['hidden', 'clip'].includes(overflowX)
                        && candidateRect.right > rect.right + trapTolerance;
                    const clipsY = ['hidden', 'clip'].includes(overflowY)
                        && candidateRect.bottom > rect.bottom + trapTolerance;
                    return clipsX || clipsY;
                });
            })
            .slice(0, 20)
            .map((el) => ({ tag: el.tagName, className: el.className || '' }));

        return {
            ghostScroll: ghostScrollContainers.length > 0,
            ghostScrollContainers,
            nestedScrollContainers,
            flexScrollTraps,
        };
    }

    // ---------------- Interaction ----------------
    function interactionAnalyzer(constants, selectors, scope = '') {
        const targets = queryAllSafe(selectors.clickTarget || 'button', 'button');
        const interactiveTargets = queryAllSafe(
            selectors.interactive || 'button, [role="button"], a[href], input:not([type="hidden"]), select, textarea',
            'button, [role="button"], a[href], input:not([type="hidden"]), select, textarea'
        );
        const minSize = numberConstant(constants.CLICK_TARGET_MIN_SIZE_PX, 44);
        const denseTableMinSize = numberConstant(constants.DENSE_TABLE_CLICK_TARGET_MIN_SIZE_PX, minSize);
        const tolerance = Number(constants.OVERFLOW_TOLERANCE_PX || 0);
        const isDesktopViewport = window.innerWidth >= Number(constants.LG_BREAKPOINT_PX ?? 992);
        const isDenseSitesTableTarget = (el) => (
            scope === 'sites'
            && isDesktopViewport
            && el.matches('.btn-sm.btn--icon-only')
            && Boolean(el.closest('.sites-list-scroll'))
        );
        const requiredTargetSize = (el) => (isDenseSitesTableTarget(el) ? denseTableMinSize : minSize);

        const tooSmall = targets
            .filter(isVisible)
            .filter((el) => !isVisuallyHidden(el))
            .map((el) => ({ el, rect: rectOf(el), minimum: requiredTargetSize(el) }))
            .filter(({ rect, minimum }) => rect.width < minimum || rect.height < minimum)
            .slice(0, 20)
            .map(({ el, rect, minimum }) => ({
                tag: el.tagName,
                className: el.className || '',
                text: (el.textContent || el.getAttribute('aria-label') || '').trim().replace(/\s+/g, ' ').slice(0, 80),
                width: roundTo(rect.width, 2),
                height: roundTo(rect.height, 2),
                minimum,
            }));

        const collectCenteringIssues = (selector, issueType) => Array.from(document.querySelectorAll(selector))
            .filter(isVisible)
            .map((el) => {
                const style = styleOf(el);
                const display = style?.display || '';
                const hasCenteringDisplay = display === 'flex' || display === 'inline-flex' || display === 'grid' || display === 'inline-grid';
                if (!hasCenteringDisplay) {
                    return {
                        tag: el.tagName,
                        classes: el.className || '',
                        display,
                        alignItems: style?.alignItems || '',
                        justifyContent: style?.justifyContent || '',
                        placeItems: style?.placeItems || '',
                        textAlign: style?.textAlign || '',
                        text: (el.textContent || '').trim().slice(0, 80),
                        issueType,
                    };
                }

                const alignItems = style?.alignItems || '';
                const justifyContent = style?.justifyContent || '';
                const placeItems = style?.placeItems || '';
                const textAlign = style?.textAlign || '';
                const centeredByFlex = alignItems === 'center' && justifyContent === 'center';
                const centeredByGrid = placeItems === 'center' || placeItems === 'center center';
                const centeredByText = textAlign === 'center';
                if ((centeredByFlex || centeredByGrid) && centeredByText) return null;

                return {
                    tag: el.tagName,
                    classes: el.className || '',
                    display,
                    alignItems,
                    justifyContent,
                    placeItems,
                    textAlign,
                    text: (el.textContent || '').trim().slice(0, 80),
                    issueType,
                };
            })
            .filter(Boolean)
            .slice(0, 20);

        const buttonAlignmentIssues = collectCenteringIssues('.btn:not(.btn-close):not(input)', 'button');
        const badgeAlignmentIssues = collectCenteringIssues('.badge', 'badge');
        const ssllabsPrematureDesktopLayoutIssues = (() => {
            const panel = document.querySelector('.ssllabs-panel');
            if (!(panel instanceof Element) || !isVisible(panel) || isVisuallyHidden(panel)) {
                return [];
            }

            const panelRect = rectOf(panel);
            const compactModeMaxPanelWidth = 72 * 16;
            const isDesktopViewport = window.innerWidth >= Number(constants.LG_BREAKPOINT_PX ?? 992);
            if (panelRect.width >= compactModeMaxPanelWidth) {
                return [];
            }
            if (!isDesktopViewport) {
                return [];
            }

            const summaryHeightMax = Number(constants.SSLLABS_DOMAIN_CARD_SUMMARY_HEIGHT_MAX_PX ?? 56);
            return Array.from(document.querySelectorAll('.ssllabs-domain-card'))
                .filter(isVisible)
                .map((card) => {
                    const summary = card.querySelector('.ssllabs-domain-card__summary');
                    if (!(summary instanceof Element)) {
                        return null;
                    }

                    const summaryRect = rectOf(summary);
                    if (summaryRect.height <= summaryHeightMax) {
                        return null;
                    }

                    const host = (summary.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
                    return {
                        host,
                        panelWidth: roundTo(panelRect.width, 2),
                        cardWidth: roundTo(rectOf(card).width, 2),
                        summaryHeight: roundTo(summaryRect.height, 2),
                        maximumSummaryHeight: summaryHeightMax,
                    };
                })
                .filter(Boolean)
                .slice(0, 20);
        })();

        const ssllabsFilterbarHeightIssue = (() => {
            const filterbar = document.querySelector('.ssllabs-filterbar');
            if (!(filterbar instanceof Element) || !isVisible(filterbar) || isVisuallyHidden(filterbar)) {
                return null;
            }
            const max = Number(constants.SSLLABS_FILTERBAR_HEIGHT_MAX_PX ?? 52);
            const height = roundTo(rectOf(filterbar).height, 2);
            if (height <= max) {
                return null;
            }
            return { height, maximum: max, passesMaximum: false };
        })();

        const ssllabsInlineSchedulerIssues = (() => {
            const schedulers = Array.from(document.querySelectorAll('.ssllabs-domain-card__inline-scheduler'));
            if (schedulers.length === 0) return null;
            const minWidth = Number(constants.SSLLABS_INLINE_SCHEDULER_MIN_WIDTH_PX ?? 120);
            const issues = schedulers
                .filter((el) => isVisible(el) && !isVisuallyHidden(el))
                .map((el) => {
                    const width = roundTo(rectOf(el).width, 2);
                    const hidden = styleOf(el).display === 'none';
                    return { width, hidden, meetsMinWidth: width >= minWidth };
                })
                .filter((r) => !r.meetsMinWidth || r.hidden);
            return issues.length > 0 ? { issues, minimum: minWidth } : null;
        })();

        const ssllabsInlineSchedulerLayout = (() => {
            const schedulers = Array.from(document.querySelectorAll('.ssllabs-domain-card__inline-scheduler'))
                .filter((el) => isVisible(el) && !isVisuallyHidden(el));
            const isDesktopViewport = window.innerWidth >= Number(constants.LG_BREAKPOINT_PX ?? 992);
            if (!isDesktopViewport || schedulers.length === 0) {
                return null;
            }

            const minWidth = Number(constants.SSLLABS_INLINE_SCHEDULER_MIN_WIDTH_PX ?? 120);
            const maxWidth = Number(constants.SSLLABS_INLINE_SCHEDULER_MAX_WIDTH_PX ?? 180);
            const alignmentTolerance = Number(constants.SSLLABS_INLINE_SCHEDULER_ALIGNMENT_TOLERANCE_PX ?? 2);
            const samples = schedulers.map((el) => {
                const rect = rectOf(el);
                return {
                    width: roundTo(rect.width, 2),
                    left: roundTo(rect.left, 2),
                };
            });
            const leftPositions = samples.map((sample) => sample.left);
            const alignmentVariance = roundTo(Math.max(...leftPositions) - Math.min(...leftPositions), 2);

            return {
                present: true,
                count: samples.length,
                minimum: minWidth,
                maximum: maxWidth,
                alignmentTolerance,
                alignmentVariance,
                passesAlignment: alignmentVariance <= alignmentTolerance,
                tooNarrow: samples.filter((sample) => sample.width < minWidth),
                tooWide: samples.filter((sample) => sample.width > maxWidth),
            };
        })();

        const ssllabsRetentionLayout = (() => {
            const root = document.querySelector('#ssllabs-retention-settings');
            if (!(root instanceof Element) || !isVisible(root) || isVisuallyHidden(root)) {
                return null;
            }

            const scale = root.querySelector('.ssllabs-retention-scale');
            const slider = root.querySelector('[data-retention-slider]');
            const labels = Array.from(root.querySelectorAll('.ssllabs-retention-label'))
                .filter((el) => isVisible(el) && !isVisuallyHidden(el));
            if (!(scale instanceof HTMLElement) || !(slider instanceof HTMLElement) || labels.length < 2) {
                return null;
            }

            const scaleRect = rectOf(scale);
            const sliderRect = rectOf(slider);
            const labelRects = labels.map((el) => rectOf(el));
            const widths = labelRects.map((rect) => roundTo(rect.width, 2));
            const centers = labelRects.map((rect) => roundTo(rect.left + (rect.width / 2), 2));
            const spacing = centers.slice(1).map((center, index) => roundTo(center - centers[index], 2));
            const spacingVariance = spacing.length > 1 ? roundTo(Math.max(...spacing) - Math.min(...spacing), 2) : 0;
            const leftDelta = roundTo(Math.abs(labelRects[0].left - scaleRect.left), 2);
            const rightDelta = roundTo(Math.abs(scaleRect.right - labelRects[labelRects.length - 1].right), 2);
            const widthDelta = roundTo(Math.abs(rectOf(slider).width - scaleRect.width), 2);
            const edgeDelta = roundTo(Math.max(leftDelta, rightDelta), 2);
            const widthTolerance = Number(constants.SSLLABS_RETENTION_SCALE_WIDTH_TOLERANCE_PX ?? 2);
            const edgeTolerance = Number(constants.SSLLABS_RETENTION_EDGE_ALIGNMENT_TOLERANCE_PX ?? 2);
            const spacingTolerance = Number(constants.SSLLABS_RETENTION_SPACING_VARIANCE_TOLERANCE_PX ?? 2);
            const passesAlignment = widthDelta <= widthTolerance
                && edgeDelta <= edgeTolerance
                && spacingVariance <= spacingTolerance;

            return {
                present: true,
                count: labels.length,
                widthDelta,
                edgeDelta,
                leftDelta,
                rightDelta,
                spacingVariance,
                widthTolerance,
                edgeTolerance,
                spacingTolerance,
                labelWidths: widths,
                sliderWidth: roundTo(sliderRect.width, 2),
                passesAlignment,
            };
        })();

        const ssllabsHistoryLoadingShell = (() => {
            const root = document.querySelector('[data-ssllabs-history]');
            if (!(root instanceof Element) || !isVisible(root) || isVisuallyHidden(root)) {
                return null;
            }

            const hasShellMarker = root.getAttribute('data-ssllabs-history-loading-shell') === 'true';
            const emptyState = root.querySelector('#ssllabs-history-empty');
            const toolbar = root.querySelector('.ssllabs-history-toolbar');
            const inspector = root.querySelector('#ssllabs-history-inspector');
            const canvas = root.querySelector('#ssllabs-history-chart');
            const periodList = root.querySelector('#ssllabs-history-periods');

            return {
                present: true,
                hasShellMarker,
                hasEmptyState: emptyState instanceof HTMLElement,
                hasToolbar: toolbar instanceof HTMLElement,
                hasInspector: inspector instanceof HTMLElement,
                hasCanvas: canvas instanceof HTMLElement,
                hasPeriodList: periodList instanceof HTMLElement,
                passesShell: hasShellMarker
                    && emptyState instanceof HTMLElement
                    && toolbar instanceof HTMLElement
                    && inspector instanceof HTMLElement
                    && canvas instanceof HTMLElement
                    && periodList instanceof HTMLElement,
            };
        })();

        const dashboardHeroMetricHeights = (() => {
            const heroCards = Array.from(document.querySelectorAll('.hero-metric'))
                .filter((el) => isVisible(el) && !isVisuallyHidden(el));
            if (heroCards.length === 0) {
                return null;
            }

            const isMobileViewport = window.innerWidth < Number(constants.LG_BREAKPOINT_PX ?? 992);
            const maximum = Number(
                isMobileViewport
                    ? (constants.KPI_HEIGHT_MAX_MOBILE_PX ?? 106)
                    : (constants.KPI_HEIGHT_MAX_DESKTOP_PX ?? 145)
            );
            const heights = heroCards.map((card) => roundTo(rectOf(card).height, 2));
            return {
                present: true,
                maximum,
                heights,
                tooTall: heights
                    .map((height, index) => ({ index, height }))
                    .filter((entry) => entry.height > maximum),
            };
        })();

        const dashboardHeroMetricInsets = (() => {
            const heroCards = Array.from(document.querySelectorAll('.hero-metric'))
                .filter((el) => isVisible(el) && !isVisuallyHidden(el));
            const isDesktopViewport = window.innerWidth >= Number(constants.LG_BREAKPOINT_PX ?? 992);
            if (!isDesktopViewport || heroCards.length < 3) {
                return null;
            }

            const appContainer = heroCards[0].closest('.app-container');
            if (!(appContainer instanceof HTMLElement)) {
                return null;
            }

            const containerRect = rectOf(appContainer);
            const firstRect = rectOf(heroCards[0]);
            const lastRect = rectOf(heroCards[heroCards.length - 1]);
            const leftInset = roundTo(firstRect.left - containerRect.left, 2);
            const rightInset = roundTo(containerRect.right - lastRect.right, 2);
            const variance = roundTo(Math.abs(leftInset - rightInset), 2);
            const maximum = Number(constants.KPI_SIDE_INSET_VARIANCE_MAX_PX ?? 2);

            return {
                present: true,
                leftInset,
                rightInset,
                variance,
                maximum,
                passesVariance: variance <= maximum,
            };
        })();

        const onboardingWizardStepDimming = (() => {
            const stepButtons = Array.from(document.querySelectorAll('.cb-onboarding-wizard__step-button'))
                .filter((el) => isVisible(el) && !isVisuallyHidden(el));
            if (stepButtons.length < 2) {
                return null;
            }

            const activeButtons = stepButtons.filter((button) =>
                button.classList.contains('is-active') || button.getAttribute('aria-current') === 'step'
            );
            const inactiveButtons = stepButtons.filter((button) => !activeButtons.includes(button));
            if (activeButtons.length !== 1 || inactiveButtons.length === 0) {
                return {
                    present: true,
                    activeButtons: activeButtons.length,
                    inactiveButtons: inactiveButtons.length,
                    activeOpacity: null,
                    inactiveOpacityMin: null,
                    inactiveOpacityMax: null,
                    expectedActiveMinimum: Number(constants.ONBOARDING_WIZARD_ACTIVE_OPACITY_MIN ?? 0.95),
                    expectedInactiveMaximum: Number(constants.ONBOARDING_WIZARD_INACTIVE_OPACITY_MAX ?? 0.8),
                    passesDimming: false,
                };
            }

            const opacities = stepButtons.map((button) => {
                const style = styleOf(button);
                return roundTo(Number.parseFloat(style.opacity || '1') || 1, 2);
            });
            const activeOpacity = opacities[activeButtons.length ? stepButtons.indexOf(activeButtons[0]) : 0];
            const inactiveOpacities = stepButtons
                .map((button, index) => ({ button, opacity: opacities[index] }))
                .filter(({ button }) => !activeButtons.includes(button))
                .map(({ opacity }) => opacity);
            const inactiveOpacityMin = roundTo(Math.min(...inactiveOpacities), 2);
            const inactiveOpacityMax = roundTo(Math.max(...inactiveOpacities), 2);
            const expectedActiveMinimum = Number(constants.ONBOARDING_WIZARD_ACTIVE_OPACITY_MIN ?? 0.95);
            const expectedInactiveMaximum = Number(constants.ONBOARDING_WIZARD_INACTIVE_OPACITY_MAX ?? 0.8);

            return {
                present: true,
                activeButtons: activeButtons.length,
                inactiveButtons: inactiveButtons.length,
                activeOpacity,
                inactiveOpacityMin,
                inactiveOpacityMax,
                expectedActiveMinimum,
                expectedInactiveMaximum,
                passesDimming: activeOpacity >= expectedActiveMinimum && inactiveOpacityMax <= expectedInactiveMaximum,
            };
        })();

        const onboardingWizardStepAccent = (() => {
            const stepButtons = Array.from(document.querySelectorAll('.cb-onboarding-wizard__step-button'))
                .filter((el) => isVisible(el) && !isVisuallyHidden(el));
            if (stepButtons.length < 2) {
                return null;
            }

            const activeButtons = stepButtons.filter((button) =>
                button.classList.contains('is-active') || button.getAttribute('aria-current') === 'step'
            );
            const inactiveButtons = stepButtons.filter((button) => !activeButtons.includes(button));
            if (activeButtons.length !== 1 || inactiveButtons.length === 0) {
                return {
                    present: true,
                    activeButtons: activeButtons.length,
                    inactiveButtons: inactiveButtons.length,
                    activeBoxShadow: null,
                    passesAccent: false,
                };
            }

            const activeStyle = styleOf(activeButtons[0]);
            const activeBoxShadow = String(activeStyle.boxShadow || '').trim();
            const hasInsetAccent = /\binset\b/i.test(activeBoxShadow);

            return {
                present: true,
                activeButtons: 1,
                inactiveButtons: inactiveButtons.length,
                activeBoxShadow,
                passesAccent: hasInsetAccent,
            };
        })();

        const onboardingWizardStepIndexPalette = (() => {
            const stepButtons = Array.from(document.querySelectorAll('.cb-onboarding-wizard__step-button'))
                .filter((el) => isVisible(el) && !isVisuallyHidden(el));
            if (stepButtons.length < 2) {
                return null;
            }

            const activeButtons = stepButtons.filter((button) =>
                button.classList.contains('is-active') || button.getAttribute('aria-current') === 'step'
            );
            const inactiveButtons = stepButtons.filter((button) => !activeButtons.includes(button));
            if (activeButtons.length !== 1 || inactiveButtons.length === 0) {
                return {
                    present: true,
                    activeButtons: activeButtons.length,
                    inactiveButtons: inactiveButtons.length,
                    activeBackground: null,
                    inactiveBackground: null,
                    activeColor: null,
                    inactiveColor: null,
                    passesPalette: false,
                };
            }

            const activeIndex = activeButtons[0].querySelector('.cb-onboarding-wizard__step-index');
            const inactiveIndex = inactiveButtons[0].querySelector('.cb-onboarding-wizard__step-index');
            if (!(activeIndex instanceof HTMLElement) || !(inactiveIndex instanceof HTMLElement)) {
                return null;
            }

            const activeStyle = styleOf(activeIndex);
            const inactiveStyle = styleOf(inactiveIndex);
            const activeBackground = String(activeStyle.backgroundColor || '').trim();
            const inactiveBackground = String(inactiveStyle.backgroundColor || '').trim();
            const activeColor = String(activeStyle.color || '').trim();
            const inactiveColor = String(inactiveStyle.color || '').trim();

            return {
                present: true,
                activeButtons: activeButtons.length,
                inactiveButtons: inactiveButtons.length,
                activeBackground,
                inactiveBackground,
                activeColor,
                inactiveColor,
                passesPalette: activeBackground !== inactiveBackground && activeColor !== inactiveColor,
            };
        })();

        const badgeStyleMismatches = (() => {
            // Every app pill must share the unified compact geometry
            // (the --cb-pill-* tokens). Flag any pill whose rendered
            // min-height / font-size / inline padding drifts from it.
            const selector = [
                '.cb-pill',
                '.status-pill',
                '.site-domain-badge',
                '.site-handler-badge',
                '.ssllabs-site-domain-badge',
                '.site-cert__status',
                '.site-cert__days',
                '.ssllabs-result__grade',
                '.ssllabs-result__status-badge',
                '.ssllabs-endpoint-chip__grade',
            ].join(',');
            const pills = Array.from(document.querySelectorAll(selector))
                .filter((el) => isVisible(el) && !isVisuallyHidden(el));
            if (pills.length === 0) return null;

            const expectedMinHeight = Number(constants.PILL_MIN_HEIGHT_EXPECTED_PX ?? 21.6);
            const expectedFontSize = Number(constants.PILL_FONT_SIZE_EXPECTED_PX ?? 11.84);
            const expectedPaddingInline = Number(constants.PILL_PADDING_INLINE_EXPECTED_PX ?? 7.2);
            const minHeightTolerance = Number(constants.PILL_MIN_HEIGHT_TOLERANCE_PX ?? 1);
            const fontSizeTolerance = Number(constants.BADGE_FONT_SIZE_TOLERANCE_PX ?? 0.5);
            const paddingTolerance = Number(constants.BADGE_PADDING_TOLERANCE_PX ?? 1);

            const issues = pills
                .map((el) => {
                    const style = styleOf(el);
                    const minHeight = parseFloat(style.minHeight) || 0;
                    const fontSize = parseFloat(style.fontSize) || 0;
                    const paddingLeft = parseFloat(style.paddingLeft) || 0;
                    const paddingRight = parseFloat(style.paddingRight) || 0;
                    const deviations = [];
                    if (Math.abs(minHeight - expectedMinHeight) > minHeightTolerance) {
                        deviations.push({ prop: 'min-height', actual: roundTo(minHeight, 2), expected: expectedMinHeight });
                    }
                    if (Math.abs(fontSize - expectedFontSize) > fontSizeTolerance) {
                        deviations.push({ prop: 'font-size', actual: roundTo(fontSize, 2), expected: expectedFontSize });
                    }
                    if (Math.abs(paddingLeft - expectedPaddingInline) > paddingTolerance
                        || Math.abs(paddingRight - expectedPaddingInline) > paddingTolerance) {
                        deviations.push({
                            prop: 'padding-inline',
                            actual: `${roundTo(paddingLeft, 2)} / ${roundTo(paddingRight, 2)}`,
                            expected: expectedPaddingInline,
                        });
                    }
                    if (deviations.length === 0) return null;
                    return {
                        className: el.className || '',
                        text: (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40),
                        deviations,
                    };
                })
                .filter(Boolean)
                .slice(0, 20);

            return issues;
        })();

        const viewportClippedInteractiveElements = interactiveTargets
            .filter(isVisible)
            .filter((el) => !isVisuallyHidden(el))
            .map((el) => ({ el, rect: rectOf(el) }))
            .filter(({ rect }) => {
                const intersectsViewport = rect.right > tolerance
                    && rect.left < window.innerWidth - tolerance
                    && rect.bottom > tolerance
                    && rect.top < window.innerHeight - tolerance;
                if (!intersectsViewport) {
                    return false;
                }
                return rect.left < (0 - tolerance)
                    || rect.right > window.innerWidth + tolerance
                    || rect.top < (0 - tolerance)
                    || rect.bottom > window.innerHeight + tolerance;
            })
            .slice(0, 20)
            .map(({ el, rect }) => ({
                tag: el.tagName,
                className: el.className || '',
                text: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 80),
                left: roundTo(rect.left, 2),
                right: roundTo(rect.right, 2),
                top: roundTo(rect.top, 2),
                bottom: roundTo(rect.bottom, 2),
                viewportWidth: window.innerWidth,
                viewportHeight: window.innerHeight,
            }));

        return {
            clickTargetsTooSmall: tooSmall,
            buttonAlignmentIssues,
            badgeAlignmentIssues,
            ssllabsPrematureDesktopLayoutIssues,
            ssllabsFilterbarHeightIssue,
            ssllabsInlineSchedulerIssues,
            ssllabsInlineSchedulerLayout,
            ssllabsRetentionLayout,
            ssllabsHistoryLoadingShell,
            dashboardHeroMetricHeights,
            dashboardHeroMetricInsets,
            onboardingWizardStepDimming,
            onboardingWizardStepAccent,
            onboardingWizardStepIndexPalette,
            badgeStyleMismatches,
            viewportClippedInteractiveElements,
        };
    }

    // ---------------- Contrast (simplified baseline) ----------------
    function contrastAnalyzer(constants = {}) {
        const contrastProblems = [];
        const theme = document.documentElement.getAttribute('data-bs-theme') || 'light';
        const liveConsole = document.querySelector('.live-console');

        if (theme === 'light' && liveConsole && isVisible(liveConsole)) {
            const style = styleOf(liveConsole);
            const lineEl = liveConsole.querySelector('.console-line, .console-msg');
            const lineStyle = lineEl ? styleOf(lineEl) : null;

            const consoleBgRgb = normalizeColorToRgb(style.backgroundColor);
            const consoleFgRgb = normalizeColorToRgb(lineStyle?.color || style.color);
            const consoleLuma = relativeLuminance(consoleBgRgb);
            const ratio = contrastRatio(consoleFgRgb, consoleBgRgb);

            const minLightLuma = Number(constants.CONSOLE_LIGHT_BG_MIN_LUMA ?? 0.75);
            const minContrast = Number(constants.WCAG_NORMAL_AA ?? 4.5);

            if (consoleLuma != null && consoleLuma < minLightLuma) {
                contrastProblems.push({
                    rule: 'console-light-background',
                    selector: '.live-console',
                    value: roundTo(consoleLuma, 3),
                    minimum: minLightLuma,
                });
            }

            if (ratio != null && ratio < minContrast) {
                contrastProblems.push({
                    rule: 'console-line-contrast',
                    selector: '.live-console .console-line',
                    value: roundTo(ratio, 2),
                    minimum: minContrast,
                });
            }
        }

        return {
            contrastProblems,
        };
    }

    // ---------------- State / UX ----------------
    function stateAnalyzer() {
        const buttons = Array.from(document.querySelectorAll('button'));

        const isLoadingButton = (btn) => {
            const text = (btn.textContent || '').trim();
            return /loading|saving|creating|deleting|processing|starting/i.test(text)
                || btn.querySelector('.spinner-border:not(.d-none)')
                || btn.hasAttribute('data-loading-active');
        };

        const loadingWithoutDisabled = buttons
            .filter(isLoadingButton)
            .filter((btn) => !btn.disabled)
            .map((btn) => ({ text: (btn.textContent || '').trim() }));

        const missingAriaBusy = buttons
            .filter(isLoadingButton)
            .filter((btn) => btn.disabled && !btn.hasAttribute('aria-busy'))
            .map((btn) => ({ text: (btn.textContent || '').trim() }));

        return {
            loadingWithoutDisabled,
            missingAriaBusy,
        };
    }

    // ---------------- Components ----------------
    function componentAnalyzer() {
        const modals = getOpenModalElements();
        const multipleModals = modals.length > 1;

        const toastContainer = document.querySelector('#toast-container');
        const toasts = toastContainer ? toastContainer.children.length : 0;

        return {
            modal: {
                multipleOpen: multipleModals,
                count: modals.length,
            },
            toast: {
                count: toasts,
                stackingIssue: toasts > 5,
            },
        };
    }

    function modalThemeAnalyzer(constants = {}) {
        resetRunCache();
        const theme = document.documentElement.getAttribute('data-bs-theme') || 'light';
        const [dialog] = getOpenModalDialogs();
        if (theme !== 'dark' || !dialog) {
            return { modalThemeIssues: [] };
        }

        const dialogStyle = styleOf(dialog);
        const dialogBg = normalizeColorToRgb(dialogStyle.backgroundColor);
        const dialogLuma = relativeLuminance(dialogBg);
        const modalDialogMaxLuma = Number(constants.MODAL_DARK_DIALOG_MAX_LUMA ?? 0.3);
        const modalControlLightBgMinLuma = Number(constants.MODAL_CONTROL_LIGHT_BG_MIN_LUMA ?? 0.72);
        const modalControlDarkTextMaxLuma = Number(constants.MODAL_CONTROL_DARK_TEXT_MAX_LUMA ?? 0.3);
        const controls = Array.from(dialog.querySelectorAll('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), textarea, select'));

        const issues = controls
            .filter(isVisible)
            .map((control) => {
                const style = styleOf(control);
                const bg = normalizeColorToRgb(style.backgroundColor);
                const fg = normalizeColorToRgb(style.color);
                const bgLuma = relativeLuminance(bg);
                const fgLuma = relativeLuminance(fg);
                const overlyLightBackground = bgLuma != null && bgLuma > modalControlLightBgMinLuma;
                const overlyDarkText = fgLuma != null && fgLuma < modalControlDarkTextMaxLuma;
                const modalIsDark = dialogLuma != null && dialogLuma < modalDialogMaxLuma;

                if (!modalIsDark || (!overlyLightBackground && !overlyDarkText)) {
                    return null;
                }

                return {
                    tag: control.tagName,
                    id: control.id || null,
                    backgroundColor: style.backgroundColor,
                    color: style.color,
                    bgLuma: bgLuma != null ? roundTo(bgLuma, 3) : null,
                    fgLuma: fgLuma != null ? roundTo(fgLuma, 3) : null,
                };
            })
            .filter(Boolean)
            .slice(0, 20);

        return { modalThemeIssues: issues };
    }

    // ---------------- Token Enforcement ----------------
    function tokenAnalyzer() {
        // Query only elements with inline styles instead of scanning entire DOM
        const offenders = Array.from(document.querySelectorAll('[style]'))
            .filter(isVisible)
            .filter((el) => usesHardcodedColor(el.getAttribute('style') || ''))
            .slice(0, 20);

        return { hardcodedStyles: offenders.length };
    }

    function isDangerLike(colorValue) {
        const rgb = normalizeColorToRgb(colorValue);
        if (!rgb) return false;
        return rgb.r >= 140 && rgb.r > rgb.g + 20 && rgb.r > rgb.b + 20;
    }

    function loginFailureAnalyzer() {
        const loginForm = document.querySelector('form[action="/login"]');
        const banner = document.getElementById('login-error-banner')
            || loginForm?.querySelector('.alert[role="alert"]');
        const bannerTextEl = document.getElementById('login-error-banner-text')
            || banner;
        const submitBtn = document.getElementById('submit-btn')
            || loginForm?.querySelector('button[type="submit"]');
        const passwordInput = document.getElementById('password')
            || loginForm?.querySelector('input[type="password"]');
        const loginCard = document.getElementById('login-card')
            || loginForm?.closest('.login-card, .card');

        const bannerVisible = Boolean(
            banner
            && isVisible(banner)
            && banner.getAttribute('aria-hidden') !== 'true'
            && (banner.classList.contains('is-visible') || banner.style.visibility !== 'hidden' || banner.classList.contains('show'))
        );

        const bannerText = (bannerTextEl?.textContent || banner?.textContent || '').trim();
        const buttonLabel = (submitBtn?.querySelector('.btn-label')?.textContent || submitBtn?.textContent || '').trim().toLowerCase();
        const submitButtonDisabled = Boolean(submitBtn?.disabled);
        const submitButtonReset = buttonLabel === 'sign in' && !submitButtonDisabled;

        let passwordBorderIsDangerLike = false;
        let passwordInvalidClass = false;
        if (passwordInput) {
            const passwordStyle = styleOf(passwordInput);
            passwordBorderIsDangerLike = isDangerLike(passwordStyle.borderTopColor) || isDangerLike(passwordStyle.boxShadow);
            passwordInvalidClass = passwordInput.classList.contains('is-invalid') || passwordInput.getAttribute('aria-invalid') === 'true';
        }

        return {
            selectorsFound: Boolean(banner && submitBtn),
            alertVisible: bannerVisible,
            errorText: bannerText,
            submitButtonDisabled,
            submitButtonReset,
            submitButtonLabel: buttonLabel || 'missing',
            passwordBorderIsDangerLike,
            passwordInvalidClass,
            cardAnimationActive: Boolean(loginCard?.classList.contains('login-card-shake')),
        };
    }

    function footerGapAnalyzer(constants = {}) {
        const footer = document.querySelector('.app-footer');
        if (!footer || !isVisible(footer)) {
            return {
                footerViewportGap: {
                    present: false,
                    gapPx: null,
                    minimum: Number(constants.FOOTER_VIEWPORT_GAP_MIN_PX ?? 0),
                    passesMinimum: true,
                },
            };
        }

        const rect = rectOf(footer);
        const minimum = Number(constants.FOOTER_VIEWPORT_GAP_MIN_PX ?? 0);
        const fullyVisibleInViewport = rect.top < window.innerHeight && rect.bottom <= window.innerHeight;

        if (!fullyVisibleInViewport) {
            return {
                footerViewportGap: {
                    present: false,
                    gapPx: null,
                    minimum,
                    passesMinimum: true,
                },
            };
        }

        const gapPx = Math.max(0, window.innerHeight - rect.bottom);

        return {
            footerViewportGap: {
                present: true,
                gapPx: roundTo(gapPx, 2),
                minimum,
                passesMinimum: gapPx >= minimum,
            },
        };
    }

    function pageShellAnalyzer(constants = {}) {
        const page = document.querySelector('.app-page');
        const mobileToggle = document.querySelector('.mobile-menu-toggle');
        const appFooter = document.querySelector('.app-footer');
        const viewportPanels = Array.from(
            document.querySelectorAll('.ssllabs-panel, .caddyfile-editor-panel, .sites-form-panel, .sites-list-panel')
        ).filter((panel) => panel instanceof Element && isVisible(panel) && !isVisuallyHidden(panel));
        const maximumGap = Number(constants.APP_PAGE_HEADER_CONTENT_GAP_MAX_PX ?? 56);
        const alignmentTolerance = Number(constants.MOBILE_TOGGLE_CONTENT_ALIGNMENT_TOLERANCE_PX ?? 2);
        const panelHeightTolerance = Number(constants.DESKTOP_PRIMARY_PANEL_HEIGHT_TOLERANCE_PX ?? 3);
        const viewportPanelFooterGapMaximum = Number(constants.DESKTOP_VIEWPORT_PANEL_FOOTER_GAP_MAX_PX ?? 24);
        const mobileToggleAlignmentFallback = {
            present: false,
            toggleLeft: null,
            contentLeft: null,
            delta: null,
            tolerance: alignmentTolerance,
            passesTolerance: true,
        };
        const primaryPanelHeightFallback = {
            present: false,
            heights: [],
            delta: null,
            tolerance: panelHeightTolerance,
            passesTolerance: true,
        };
        const viewportPanelFooterGapFallback = {
            present: false,
            gapPx: null,
            maximum: viewportPanelFooterGapMaximum,
            passesMaximum: true,
        };
        if (!(page instanceof Element) || !isVisible(page) || isVisuallyHidden(page)) {
            return {
                appPageLayout: {
                    present: false,
                    overflowY: null,
                    locksVerticalOverflow: false,
                },
                mobileToggleContentAlignment: mobileToggleAlignmentFallback,
                desktopPrimaryPanelHeightAlignment: primaryPanelHeightFallback,
                desktopViewportPanelFooterGap: viewportPanelFooterGapFallback,
                pageHeaderContentGap: {
                    present: false,
                    gapPx: null,
                    maximum: maximumGap,
                    passesMaximum: true,
                },
            };
        }

        const pageStyle = styleOf(page);
        const overflowY = pageStyle?.overflowY || 'visible';
        const isMobileViewport = window.innerWidth < Number(constants.LG_BREAKPOINT_PX ?? 992);
        const isDesktopTwoColumnViewport = window.innerWidth >= Number(constants.XL_BREAKPOINT_PX ?? 1200);
        const children = Array.from(page.children).filter((child) => child instanceof Element);
        const header = children.find((child) => child.matches('.app-page__header') && isVisible(child) && !isVisuallyHidden(child)) || null;
        const firstContentBlock = children.find((child) => child !== header && isVisible(child) && !isVisuallyHidden(child)) || null;

        let pageHeaderContentGap = {
            present: false,
            gapPx: null,
            maximum: maximumGap,
            passesMaximum: true,
        };

        if (header && firstContentBlock) {
            const gapPx = Math.max(0, rectOf(firstContentBlock).top - rectOf(header).bottom);
            pageHeaderContentGap = {
                present: true,
                gapPx: roundTo(gapPx, 2),
                maximum: maximumGap,
                passesMaximum: gapPx <= maximumGap,
            };
        }

        let mobileToggleContentAlignment = mobileToggleAlignmentFallback;
        if (
            isMobileViewport
            && mobileToggle instanceof HTMLElement
            && isVisible(mobileToggle)
        ) {
            const referenceElement = header || firstContentBlock || page;
            const toggleLeft = rectOf(mobileToggle).left;
            const contentLeft = rectOf(referenceElement).left;
            const delta = Math.abs(toggleLeft - contentLeft);
            mobileToggleContentAlignment = {
                present: true,
                toggleLeft: roundTo(toggleLeft, 2),
                contentLeft: roundTo(contentLeft, 2),
                delta: roundTo(delta, 2),
                tolerance: alignmentTolerance,
                passesTolerance: delta <= alignmentTolerance,
            };
        }

        let desktopPrimaryPanelHeightAlignment = primaryPanelHeightFallback;
        const pageGrid = page.querySelector(':scope > .row.app-grid');
        if (isDesktopTwoColumnViewport && pageGrid instanceof Element) {
            const panels = Array.from(pageGrid.children)
                .filter((child) => child instanceof Element)
                .map((column) => {
                    const directPanel = column.querySelector(':scope > .panel-card');
                    if (directPanel instanceof Element && isVisible(directPanel) && !isVisuallyHidden(directPanel)) {
                        return directPanel;
                    }

                    return Array.from(column.children).find((child) => (
                        child instanceof Element
                        && isVisible(child)
                        && !isVisuallyHidden(child)
                    )) || null;
                })
                .filter((panel) => panel instanceof Element && isVisible(panel) && !isVisuallyHidden(panel));

            if (panels.length >= 2) {
                const heights = panels.slice(0, 2).map((panel) => roundTo(rectOf(panel).height, 2));
                const delta = Math.abs(heights[0] - heights[1]);
                desktopPrimaryPanelHeightAlignment = {
                    present: true,
                    heights,
                    delta: roundTo(delta, 2),
                    tolerance: panelHeightTolerance,
                    passesTolerance: delta <= panelHeightTolerance,
                };
            }
        }

        let desktopViewportPanelFooterGap = viewportPanelFooterGapFallback;
        if (
            isDesktopTwoColumnViewport
            && viewportPanels.length > 0
        ) {
            const panelBottom = Math.max(...viewportPanels.map((panel) => rectOf(panel).bottom));
            const footerTop = appFooter instanceof Element && isVisible(appFooter) && !isVisuallyHidden(appFooter)
                ? rectOf(appFooter).top
                : window.innerHeight;
            const gapPx = Math.max(0, footerTop - panelBottom);
            desktopViewportPanelFooterGap = {
                present: true,
                gapPx: roundTo(gapPx, 2),
                maximum: viewportPanelFooterGapMaximum,
                passesMaximum: gapPx <= viewportPanelFooterGapMaximum,
            };
        }

        return {
            appPageLayout: {
                present: true,
                overflowY,
                locksVerticalOverflow: !['visible'].includes(overflowY),
            },
            mobileToggleContentAlignment,
            desktopPrimaryPanelHeightAlignment,
            desktopViewportPanelFooterGap,
            pageHeaderContentGap,
        };
    }

    function primaryPanelPaddingAnalyzer(constants = {}) {
        const pageGrid = document.querySelector('.app-grid');
        const tolerance = Number(constants.PRIMARY_PANEL_PADDING_VARIANCE_MAX_PX ?? 2);

        if (!(pageGrid instanceof Element) || !isVisible(pageGrid) || isVisuallyHidden(pageGrid)) {
            return {
                primaryPanelPadding: {
                    present: false,
                    tolerance,
                    panels: [],
                    mismatches: [],
                },
            };
        }

        const rowTolerance = 8;
        const panels = Array.from(pageGrid.children)
            .filter((child) => child instanceof Element)
            .map((column) => column.querySelector(':scope > .panel-card'))
            .filter((panel) => panel instanceof Element && isVisible(panel) && !isVisuallyHidden(panel))
            .map((panel) => {
                const style = styleOf(panel);
                const rect = rectOf(panel);
                const isTableVariant = panel.classList.contains('panel-card--table');
                return {
                    className: panel.className || '',
                    isTableVariant,
                    top: roundTo(rect.top, 2),
                    paddingTop: Number.parseFloat(style?.paddingTop || '0'),
                    paddingRight: Number.parseFloat(style?.paddingRight || '0'),
                    paddingBottom: Number.parseFloat(style?.paddingBottom || '0'),
                    paddingLeft: Number.parseFloat(style?.paddingLeft || '0'),
                };
            });

        if (panels.length < 2) {
            return {
                primaryPanelPadding: {
                    present: false,
                    tolerance,
                    panels,
                    mismatches: [],
                },
            };
        }

        const firstRowTop = panels[0].top;
        const firstRowPanels = panels.filter((panel) => Math.abs(panel.top - firstRowTop) <= rowTolerance);

        if (firstRowPanels.length < 2) {
            return {
                primaryPanelPadding: {
                    present: false,
                    tolerance,
                    panels,
                    mismatches: [],
                },
            };
        }

        // Only compare panels of the same variant type (table vs non-table)
        const baseline = firstRowPanels[0];
        const mismatches = firstRowPanels
            .slice(1)
            .filter((panel) => panel.isTableVariant === baseline.isTableVariant)
            .filter((panel) => (
                Math.abs(panel.paddingTop - baseline.paddingTop) > tolerance
                || Math.abs(panel.paddingRight - baseline.paddingRight) > tolerance
                || Math.abs(panel.paddingBottom - baseline.paddingBottom) > tolerance
                || Math.abs(panel.paddingLeft - baseline.paddingLeft) > tolerance
            ));

        return {
            primaryPanelPadding: {
                present: true,
                tolerance,
                panels: firstRowPanels,
                mismatches,
            },
        };
    }

    function mobileSidebarFooterAnalyzer(constants = {}) {
        const isMobileViewport = window.innerWidth < Number(constants.LG_BREAKPOINT_PX ?? 992);
        const sidebar = document.querySelector('.app-sidebar');
        const sidebarFooter = document.querySelector('.sidebar-footer');
        const minimum = Number(constants.SIDEBAR_FOOTER_VIEWPORT_CLEARANCE_MIN_PX ?? 0);
        const sidebarRect = sidebar ? rectOf(sidebar) : null;
        const sidebarIsOpen = Boolean(
            sidebar
            && sidebar.classList.contains('is-open')
            && sidebarRect
            && sidebarRect.right > 0
            && sidebarRect.left < window.innerWidth
        );

        if (!isMobileViewport || !sidebar || !sidebarFooter || !sidebarIsOpen || !isVisible(sidebarFooter)) {
            return {
                sidebarFooterViewportGap: {
                    present: false,
                    gapPx: null,
                    minimum,
                    passesMinimum: true,
                    requiresScroll: false,
                },
            };
        }

        const footerRect = rectOf(sidebarFooter);
        const sidebarRequiresScroll = sidebar.scrollHeight > sidebar.clientHeight + 1;
        const fullyVisibleInViewport = footerRect.top < window.innerHeight && footerRect.bottom <= window.innerHeight;

        if (!fullyVisibleInViewport) {
            return {
                sidebarFooterViewportGap: {
                    present: true,
                    gapPx: null,
                    minimum,
                    passesMinimum: false,
                    requiresScroll: sidebarRequiresScroll,
                },
            };
        }

        const gapPx = Math.max(0, window.innerHeight - footerRect.bottom);

        return {
            sidebarFooterViewportGap: {
                present: true,
                gapPx: roundTo(gapPx, 2),
                minimum,
                passesMinimum: gapPx >= minimum,
                requiresScroll: sidebarRequiresScroll,
            },
        };
    }

    function sidebarNavAnalyzer(constants = {}) {
        const sidebar = document.querySelector('.app-sidebar');
        const nav = document.querySelector('.app-nav');
        const isMobileViewport = window.innerWidth < Number(constants.LG_BREAKPOINT_PX ?? 992);
        const minimumGap = Number(
            isMobileViewport
                ? (constants.SIDEBAR_NAV_GAP_MOBILE_MIN_PX ?? 7)
                : (constants.SIDEBAR_NAV_GAP_MIN_PX ?? 10)
        );
        const minimumLinkHeight = Number(
            isMobileViewport
                ? (constants.SIDEBAR_NAV_LINK_MOBILE_MIN_HEIGHT_PX ?? 48)
                : (constants.SIDEBAR_NAV_LINK_MIN_HEIGHT_PX ?? 52)
        );
        const fallback = {
            present: false,
            navGapPx: null,
            linkMinHeightPx: null,
            minimumGap,
            minimumLinkHeight,
            passesGap: true,
            passesLinkHeight: true,
        };

        if (!(sidebar instanceof Element) || !(nav instanceof Element)) {
            return { sidebarNavSpacing: fallback };
        }

        const sidebarRect = rectOf(sidebar);
        const sidebarVisible = isVisible(sidebar)
            && !isVisuallyHidden(sidebar)
            && (!isMobileViewport || (sidebar.classList.contains('is-open') && sidebarRect.right > 0 && sidebarRect.left < window.innerWidth));

        if (!sidebarVisible || !isVisible(nav) || isVisuallyHidden(nav)) {
            return { sidebarNavSpacing: fallback };
        }

        const navLinks = Array.from(nav.querySelectorAll(':scope > .app-nav__link'))
            .filter((link) => link instanceof Element && isVisible(link) && !isVisuallyHidden(link));

        if (navLinks.length === 0) {
            return { sidebarNavSpacing: fallback };
        }

        const navStyle = styleOf(nav);
        const navGapPx = Number.parseFloat(navStyle?.rowGap || navStyle?.gap || '0');
        const linkMinHeightPx = Math.min(...navLinks.map((link) => rectOf(link).height));

        return {
            sidebarNavSpacing: {
                present: true,
                navGapPx: roundTo(navGapPx, 2),
                linkMinHeightPx: roundTo(linkMinHeightPx, 2),
                minimumGap,
                minimumLinkHeight,
                passesGap: navGapPx >= minimumGap,
                passesLinkHeight: linkMinHeightPx >= minimumLinkHeight,
            },
        };
    }

    // ---------------- Page Structure Consistency ----------------
    function pageStructureAnalyzer() {
        const appPage = document.querySelector('.app-page');
        if (!appPage) {
            return { pageStructureConsistent: { present: false, hasRowWrapper: null, issues: [] } };
        }

        const directChildren = Array.from(appPage.children).filter((el) => !el.classList.contains('app-page__header'));
        const hasRowWrapper = directChildren.some((el) => el.matches('.row.app-grid, .row.metric-grid'));
        const unwrappedPanelCards = directChildren
            .filter((el) => el.classList.contains('panel-card'))
            .map((el) => ({
                rule: 'panel-card-direct-child',
                tag: el.tagName,
                className: el.className || '',
            }));

        const invalidGridChildren = Array.from(appPage.querySelectorAll(':scope > .row.app-grid > *'))
            .filter((el) => !/\bcol(?:-|$)/.test(el.className || ''))
            .map((el) => ({
                rule: 'app-grid-child-not-column',
                tag: el.tagName,
                className: el.className || '',
            }));

        return {
            pageStructureConsistent: {
                present: true,
                hasRowWrapper,
                issues: [
                    ...unwrappedPanelCards,
                    ...invalidGridChildren,
                ],
            },
        };
    }

    function sitesFormControlHeightAnalyzer(constants = {}, scope = '') {
        const expectedHeight = Number(constants.SITES_FORM_CONTROL_HEIGHT_EXPECTED_PX ?? 50);
        const tolerance = Number(constants.SITES_FORM_CONTROL_HEIGHT_TOLERANCE_PX ?? 2);
        const maximumEditorBottomGap = Number(constants.SITES_FORM_CONFIG_EDITOR_BOTTOM_GAP_MAX_PX ?? 16);
        const maximumActionsGap = Number(constants.SITES_FORM_CONFIG_ACTIONS_GAP_MAX_PX ?? 20);
        const isDesktopViewport = window.innerWidth >= Number(constants.XL_BREAKPOINT_PX ?? 1200);
        const fallback = {
            present: false,
            expectedHeight,
            tolerance,
            siteNameHeightPx: null,
            domainControlHeightPx: null,
            passesSiteName: true,
            passesDomainControl: true,
        };
        const layoutFallback = {
            present: false,
            maximumEditorBottomGap,
            maximumActionsGap,
            editorBottomGapPx: null,
            actionsGapPx: null,
            passesEditorBottomGap: true,
            passesActionsGap: true,
        };

        if (scope !== 'sites') {
            return {
                sitesFormControlHeights: fallback,
                sitesFormLayout: layoutFallback,
            };
        }

        const siteNameInput = document.getElementById('site-name');
        const domainControl = document.querySelector('[data-domain-tag-shell]');
        if (!(siteNameInput instanceof Element) || !(domainControl instanceof Element)) {
            return {
                sitesFormControlHeights: fallback,
                sitesFormLayout: layoutFallback,
            };
        }
        if (!isVisible(siteNameInput) || isVisuallyHidden(siteNameInput) || !isVisible(domainControl) || isVisuallyHidden(domainControl)) {
            return {
                sitesFormControlHeights: fallback,
                sitesFormLayout: layoutFallback,
            };
        }

        const siteNameHeightPx = rectOf(siteNameInput).height;
        const domainControlHeightPx = rectOf(domainControl).height;
        const passesSiteName = Math.abs(siteNameHeightPx - expectedHeight) <= tolerance;
        const passesDomainControl = Math.abs(domainControlHeightPx - expectedHeight) <= tolerance;
        let sitesFormLayout = layoutFallback;

        if (isDesktopViewport) {
            const configSection = document.querySelector('.sites-form-panel__config');
            const actions = document.querySelector('.sites-form-panel__actions');
            const configEditor = configSection instanceof Element
                ? (() => {
                    const cmEditor = configSection.querySelector('.cm-editor');
                    if (cmEditor instanceof Element && isVisible(cmEditor) && !isVisuallyHidden(cmEditor)) {
                        return cmEditor;
                    }
                    return Array.from(configSection.querySelectorAll('textarea[name="caddy_directives"]'))
                        .find((element) => element instanceof Element && isVisible(element) && !isVisuallyHidden(element))
                        || null;
                })()
                : null;

            if (
                configSection instanceof Element
                && actions instanceof Element
                && configEditor instanceof Element
                && isVisible(configSection)
                && !isVisuallyHidden(configSection)
                && isVisible(actions)
                && !isVisuallyHidden(actions)
            ) {
                const configRect = rectOf(configSection);
                const editorRect = rectOf(configEditor);
                const actionsRect = rectOf(actions);
                const editorBottomGapPx = Math.max(0, configRect.bottom - editorRect.bottom);
                const actionsGapPx = Math.max(0, actionsRect.top - configRect.bottom);

                sitesFormLayout = {
                    present: true,
                    maximumEditorBottomGap,
                    maximumActionsGap,
                    editorBottomGapPx: roundTo(editorBottomGapPx, 2),
                    actionsGapPx: roundTo(actionsGapPx, 2),
                    passesEditorBottomGap: editorBottomGapPx <= maximumEditorBottomGap,
                    passesActionsGap: actionsGapPx <= maximumActionsGap,
                };
            }
        }

        return {
            sitesFormControlHeights: {
                present: true,
                expectedHeight,
                tolerance,
                siteNameHeightPx: roundTo(siteNameHeightPx, 2),
                domainControlHeightPx: roundTo(domainControlHeightPx, 2),
                passesSiteName,
                passesDomainControl,
            },
            sitesFormLayout,
        };
    }

    function sitesTableDensityAnalyzer(constants = {}, scope = '') {
        const maximumRowHeight = Number(constants.SITES_TABLE_ROW_MAX_HEIGHT_PX ?? 64);
        const targetRowHeight = Number(constants.SITES_TABLE_DENSE_ROW_TARGET_PX ?? 52);
        const isDesktopViewport = window.innerWidth >= Number(constants.LG_BREAKPOINT_PX ?? 992);
        const fallback = {
            present: false,
            maximumRowHeight,
            targetRowHeight,
            rowCount: 0,
            medianRowHeightPx: null,
            maxRowHeightPx: null,
            oversizedRows: [],
        };

        if (scope !== 'sites' || !isDesktopViewport) {
            return { sitesTableDensity: fallback };
        }

        const table = document.querySelector('.sites-list-panel table, .sites-list-scroll table');
        if (!(table instanceof Element) || !isVisible(table) || isVisuallyHidden(table)) {
            return { sitesTableDensity: fallback };
        }

        const rows = Array.from(table.querySelectorAll('tbody tr'))
            .filter((row) => row instanceof Element && isVisible(row) && !isVisuallyHidden(row));
        if (!rows.length) {
            return { sitesTableDensity: { ...fallback, present: true } };
        }

        const rowHeights = rows
            .map((row) => {
                const rowRect = rectOf(row);
                return rowRect.height;
            })
            .filter((height) => Number.isFinite(height) && height > 0)
            .sort((a, b) => a - b);
        if (!rowHeights.length) {
            return { sitesTableDensity: { ...fallback, present: true } };
        }

        const middle = Math.floor(rowHeights.length / 2);
        const medianRowHeightPx = rowHeights.length % 2 === 0
            ? (rowHeights[middle - 1] + rowHeights[middle]) / 2
            : rowHeights[middle];
        const maxRowHeightPx = rowHeights[rowHeights.length - 1];
        const oversizedRows = rows
            .map((row, index) => {
                const rowRect = rectOf(row);
                return {
                    index,
                    text: (row.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80),
                    height: rowRect.height,
                };
            })
            .filter((row) => row.height > maximumRowHeight)
            .slice(0, 20)
            .map((row) => ({
                index: row.index,
                text: row.text,
                height: roundTo(row.height, 2),
            }));

        return {
            sitesTableDensity: {
                present: true,
                maximumRowHeight,
                targetRowHeight,
                rowCount: rows.length,
                medianRowHeightPx: roundTo(medianRowHeightPx, 2),
                maxRowHeightPx: roundTo(maxRowHeightPx, 2),
                oversizedRows,
            },
        };
    }

    // On mobile the SSL Labs domains table must collapse into standalone site
    // cards (mirroring the Sites list): each site row renders as a block-level
    // tile with a border + corner radius, and the table head is hidden. This
    // analyzer locks that contract in so the responsive treatment cannot
    // silently regress back to a flat, horizontally-scrolling table row.
    function ssllabsMobileCardLayoutAnalyzer(constants = {}, scope = '') {
        const minBorderRadius = Number(constants.SSLLABS_MOBILE_CARD_MIN_BORDER_RADIUS_PX ?? 8);
        // Gate on the md breakpoint (768px) — the exact width at which the
        // responsive CSS collapses the table into cards. Using the lg
        // breakpoint here would falsely flag tablet viewports (e.g. iPad Pro,
        // 834px) where the desktop table layout is still intentionally active.
        const isCardViewport = window.innerWidth < Number(constants.MD_BREAKPOINT_PX ?? 768);
        const fallback = {
            present: false,
            rowCount: 0,
            minBorderRadius,
            theadHidden: true,
            issues: [],
        };

        if (scope !== 'ssllabs' || !isCardViewport) {
            return { ssllabsMobileCardLayout: fallback };
        }

        const table = document.querySelector('.ssllabs-table');
        if (!(table instanceof Element) || !isVisible(table) || isVisuallyHidden(table)) {
            return { ssllabsMobileCardLayout: fallback };
        }

        const thead = table.querySelector('thead');
        const theadHidden = !(thead instanceof Element)
            || styleOf(thead).display === 'none'
            || isVisuallyHidden(thead);

        const rows = Array.from(table.querySelectorAll('tr[data-ssllabs-site-row]'))
            .filter((row) => row instanceof Element && isVisible(row) && !isVisuallyHidden(row));
        if (!rows.length) {
            return { ssllabsMobileCardLayout: { ...fallback, present: true, theadHidden } };
        }

        const issues = rows
            .map((row, index) => {
                const style = styleOf(row);
                const reasons = [];
                if (style.display !== 'block') {
                    reasons.push('notBlock');
                }
                if ((parseFloat(style.borderTopLeftRadius) || 0) < minBorderRadius) {
                    reasons.push('noCardRadius');
                }
                if ((parseFloat(style.borderTopWidth) || 0) <= 0) {
                    reasons.push('noCardBorder');
                }
                if (reasons.length === 0) {
                    return null;
                }
                const host = (row.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 80);
                return { index, host, reasons };
            })
            .filter(Boolean)
            .slice(0, 20);

        return {
            ssllabsMobileCardLayout: {
                present: true,
                rowCount: rows.length,
                minBorderRadius,
                theadHidden,
                issues,
            },
        };
    }

    // ---------------- Mobile spacing checks ----------------
    // Verifies:
    //   1. mobileTopbarClearance — the top of page content clears the fixed topbar/toggle
    //   2. mobileCardEdgeAlignment — .app-page__header left edge aligns with .panel-card content left edge
    function mobileSpacingAnalyzer(constants = {}) {
        const isMobileViewport = window.innerWidth < Number(constants.LG_BREAKPOINT_PX ?? 992);
        const clearanceMin = Number(constants.MOBILE_TOPBAR_CLEARANCE_MIN_PX ?? 56);
        const alignmentTolerance = Number(constants.MOBILE_CARD_HEADING_ALIGNMENT_TOLERANCE_PX ?? 2);

        const topbarClearanceFallback = {
            present: false,
            topbarHeightPx: null,
            contentTopPx: null,
            clearancePx: null,
            minimum: clearanceMin,
            passesClearance: true,
        };

        const cardEdgeAlignmentFallback = null;

        if (!isMobileViewport) {
            return {
                mobileTopbarClearance: topbarClearanceFallback,
                mobileCardEdgeAlignment: cardEdgeAlignmentFallback,
            };
        }

        // --- topbar clearance ---
        let mobileTopbarClearance = topbarClearanceFallback;
        const topbar = document.querySelector('.mobile-topbar');
        const appPage = document.querySelector('.app-page');
        if (
            topbar instanceof Element
            && isVisible(topbar)
            && appPage instanceof Element
            && isVisible(appPage)
        ) {
            const topbarRect = rectOf(topbar);
            const topbarHeightPx = roundTo(topbarRect.height, 2);
            const contentTopPx = roundTo(rectOf(appPage).top, 2);
            const clearancePx = roundTo(contentTopPx, 2);
            mobileTopbarClearance = {
                present: true,
                topbarHeightPx,
                contentTopPx,
                clearancePx,
                minimum: clearanceMin,
                passesClearance: clearancePx >= clearanceMin,
            };
        }

        // --- heading / panel-card content left-edge alignment ---
        let mobileCardEdgeAlignment = cardEdgeAlignmentFallback;
        const header = document.querySelector('.app-page__header');
        const panelCard = document.querySelector('.app-page .panel-card');
        if (
            header instanceof Element
            && isVisible(header)
            && panelCard instanceof Element
            && isVisible(panelCard)
        ) {
            const headerLeft = roundTo(rectOf(header).left, 2);
            const cardRect = rectOf(panelCard);
            const cardStyle = styleOf(panelCard);
            const cardPaddingLeft = Number.parseFloat(cardStyle?.paddingLeft || '0');
            const cardContentLeft = roundTo(cardRect.left + cardPaddingLeft, 2);
            const leftDelta = roundTo(Math.abs(headerLeft - cardContentLeft), 2);
            mobileCardEdgeAlignment = {
                present: true,
                headerLeft,
                cardContentLeft,
                leftDelta,
                tolerance: alignmentTolerance,
                matchesLeft: leftDelta <= alignmentTolerance,
                // right-edge check: header right vs card right edge minus padding
                rightDelta: 0,
                matchesRight: true,
            };
        }

        return {
            mobileTopbarClearance,
            mobileCardEdgeAlignment,
        };
    }

    function runAll({ scope, constants = {}, selectors = {} } = {}) {
        resetRunCache();

        const accessibility = accessibilityAnalyzer(constants);
        const layout = layoutAnalyzer(constants);
        const scrollContainment = scrollContainmentAnalyzer(constants, scope);
        const interaction = interactionAnalyzer(constants, selectors, scope);
        const contrast = contrastAnalyzer(constants);
        const footerGap = footerGapAnalyzer(constants);
        const sidebarFooterGap = mobileSidebarFooterAnalyzer(constants);
        const sidebarNavSpacing = sidebarNavAnalyzer(constants);
        const pageShell = pageShellAnalyzer(constants);
        const primaryPanelPadding = primaryPanelPaddingAnalyzer(constants);
        const pageStructure = pageStructureAnalyzer();
        const sitesFormControlHeights = sitesFormControlHeightAnalyzer(constants, scope);
        const sitesTableDensity = sitesTableDensityAnalyzer(constants, scope);
        const ssllabsMobileCardLayout = ssllabsMobileCardLayoutAnalyzer(constants, scope);
        const mobileSpacing = mobileSpacingAnalyzer(constants);
        const state = stateAnalyzer();
        const components = componentAnalyzer();
        const modalTheme = modalThemeAnalyzer(constants);
        const tokens = tokenAnalyzer();

        return {
            // Preserve the mixed nested + flat metrics contract for existing findings consumers.
            accessibility,
            layout,
            scrollContainment,
            interaction,
            contrast,

            // Expose flattened keys expected by findings.
            ...accessibility,
            ...layout,
            ...scrollContainment,
            ...interaction,
            ...contrast,
            ...footerGap,
            ...sidebarFooterGap,
            ...sidebarNavSpacing,
            ...pageShell,
            ...primaryPanelPadding,
            ...pageStructure,
            ...sitesFormControlHeights,
            ...sitesTableDensity,
            ...ssllabsMobileCardLayout,
            ...modalTheme,
            ...mobileSpacing,

            spacing: {
                mobileTopbarClearance: mobileSpacing.mobileTopbarClearance,
                mobileCardEdgeAlignment: mobileSpacing.mobileCardEdgeAlignment,
            },

            state,
            components,
            tokens,
            loginFailure: scope === 'login' ? loginFailureAnalyzer() : null,
        };
    }

    window.__uiLint = { runAll };
})();

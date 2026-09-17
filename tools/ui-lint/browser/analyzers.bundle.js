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

    // Collapses whitespace and truncates text for finding details.
    function compactText(value, maxLength = 80) {
        return String(value || '').trim().replace(/\s+/g, ' ').slice(0, maxLength);
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
        const hasFinePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
        const isDenseManagementTableTarget = (el) => (
            isDesktopViewport
            && hasFinePointer
            && el.matches('.btn-sm.btn--icon-only, select.form-select-sm')
            && Boolean(el.closest('table.table--management'))
        );
        const isDenseManagementToolbarTarget = (el) => (
            scope === 'ssllabs'
            && isDesktopViewport
            && hasFinePointer
            && Boolean(el.closest('.ssllabs-filterbar'))
        );
        const chipRemoveMinSize = numberConstant(constants.CHIP_REMOVE_CLICK_TARGET_MIN_SIZE_PX, minSize);
        const requiredTargetSize = (el) => {
            if (isDenseManagementTableTarget(el) || isDenseManagementToolbarTarget(el)) {
                return denseTableMinSize;
            }
            if (el.matches('.tag-input__remove')) return chipRemoveMinSize;
            return minSize;
        };
        // A transparent, absolutely positioned ::before/::after may enlarge the
        // tappable area beyond the element box; measure whichever is larger.
        const effectiveTargetSize = (el) => {
            const rect = rectOf(el);
            let width = rect.width;
            let height = rect.height;
            for (const pseudo of ['::before', '::after']) {
                const pseudoStyle = window.getComputedStyle(el, pseudo);
                if (!pseudoStyle || pseudoStyle.content === 'none' || pseudoStyle.content === 'normal') continue;
                if (pseudoStyle.position !== 'absolute' || pseudoStyle.pointerEvents === 'none') continue;
                width = Math.max(width, parseFloat(pseudoStyle.width) || 0);
                height = Math.max(height, parseFloat(pseudoStyle.height) || 0);
            }
            return { width, height };
        };

        const tooSmall = targets
            .filter(isVisible)
            .filter((el) => !isVisuallyHidden(el))
            .map((el) => ({ el, size: effectiveTargetSize(el), minimum: requiredTargetSize(el) }))
            .filter(({ size, minimum }) => size.width < minimum || size.height < minimum)
            .slice(0, 20)
            .map(({ el, size, minimum }) => ({
                tag: el.tagName,
                className: el.className || '',
                text: compactText(el.textContent || el.getAttribute('aria-label')),
                width: roundTo(size.width, 2),
                height: roundTo(size.height, 2),
                minimum,
            }));

        // iOS Safari zooms the page when a form field under 16px gains focus.
        // Only meaningful on touch-first devices, where app.css lifts inputs to 1rem.
        const inputZoomRisks = (() => {
            if (!window.matchMedia('(hover: none) and (pointer: coarse)').matches) return [];
            const minFontSize = numberConstant(constants.TOUCH_INPUT_MIN_FONT_SIZE_PX, 16);
            const nonTextTypes = new Set(['hidden', 'checkbox', 'radio', 'range', 'color', 'file', 'button', 'submit', 'reset', 'image']);
            return Array.from(document.querySelectorAll('input, select, textarea'))
                .filter((el) => !(el instanceof HTMLInputElement) || !nonTextTypes.has((el.type || 'text').toLowerCase()))
                .filter((el) => !el.disabled && !el.readOnly)
                .filter(isVisible)
                .filter((el) => !isVisuallyHidden(el))
                .map((el) => ({ el, fontSize: parseFloat(styleOf(el)?.fontSize) || 0 }))
                .filter(({ fontSize }) => fontSize > 0 && fontSize < minFontSize - 0.01)
                .slice(0, 20)
                .map(({ el, fontSize }) => ({
                    tag: el.tagName,
                    id: el.id || '',
                    className: el.className || '',
                    fontSize: roundTo(fontSize, 2),
                    minimum: minFontSize,
                }));
        })();

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

                    const host = compactText(summary.textContent);
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
            const max = Number(constants.SSLLABS_FILTERBAR_HEIGHT_MAX_PX ?? 60);
            const height = roundTo(rectOf(filterbar).height, 2);
            if (height <= max) {
                return null;
            }
            return { height, maximum: max, passesMaximum: false };
        })();

        const ssllabsInlineSchedulerIssues = (() => {
            const schedulers = Array.from(document.querySelectorAll('.ssllabs-schedule-form__controls'));
            if (schedulers.length === 0) return null;
            const minWidth = Number(constants.SSLLABS_INLINE_SCHEDULER_MIN_WIDTH_PX ?? 220);
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
            const schedulers = Array.from(document.querySelectorAll('.ssllabs-schedule-form__controls'))
                .filter((el) => isVisible(el) && !isVisuallyHidden(el));
            const isDesktopViewport = window.innerWidth >= Number(constants.LG_BREAKPOINT_PX ?? 992);
            if (!isDesktopViewport || schedulers.length === 0) {
                return null;
            }

            const minWidth = Number(constants.SSLLABS_INLINE_SCHEDULER_MIN_WIDTH_PX ?? 220);
            const maxWidth = Number(constants.SSLLABS_INLINE_SCHEDULER_MAX_WIDTH_PX ?? 340);
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
                        text: compactText(el.textContent, 40),
                        deviations,
                    };
                })
                .filter(Boolean)
                .slice(0, 20);

            return issues;
        })();

        const aboutValueFontSizeMismatches = (() => {
            // The About page renders value cells in three separate tables
            // (Application Details, Check for Updates, Dependencies); all
            // must share one font size (--about-meta-value / .about-deps-table code).
            const selector = '.about-meta-value, .about-deps-table code';
            const values = Array.from(document.querySelectorAll(selector))
                .filter((el) => isVisible(el) && !isVisuallyHidden(el));
            if (values.length === 0) return null;

            const expected = Number(constants.ABOUT_VALUE_FONT_SIZE_EXPECTED_PX ?? 14);
            const tolerance = Number(constants.ABOUT_VALUE_FONT_SIZE_TOLERANCE_PX ?? 0.5);

            return values
                .map((el) => {
                    const fontSize = parseFloat(styleOf(el).fontSize) || 0;
                    if (Math.abs(fontSize - expected) <= tolerance) return null;
                    return {
                        className: el.className || '',
                        text: (el.textContent || '').trim().slice(0, 40),
                        fontSize: roundTo(fontSize, 2),
                        expected,
                    };
                })
                .filter(Boolean)
                .slice(0, 20);
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
            inputZoomRisks,
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
            aboutValueFontSizeMismatches,
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

        const toastContainer = document.querySelector('.app-toast-stack');
        const toasts = toastContainer
            ? Array.from(toastContainer.children)
                .filter((toast) => isVisible(toast) && !isVisuallyHidden(toast))
                .length
            : 0;

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

    function loginFailureAnalyzer(loginFailureAlertSelectors = []) {
        const loginForm = document.querySelector('form[action="/login"]');
        // app/templates/login.html renders the failure alert as
        // .alert.alert-danger.login-error[data-testid="login-error"] (no
        // dedicated banner id) — match the same selector list the Node-side
        // probe uses (lib/constants.mjs LOGIN_FAILURE_ALERT_SELECTORS) so both
        // detection paths stay in sync with the template.
        const banner = loginFailureAlertSelectors
            .map((selector) => document.querySelector(selector))
            .find(Boolean)
            || loginForm?.querySelector('.alert[role="alert"]');
        const bannerTextEl = banner;
        const submitBtn = loginForm?.querySelector('button[type="submit"]');
        const passwordInput = document.getElementById('password')
            || loginForm?.querySelector('input[type="password"]');
        const loginCard = loginForm?.closest('.auth-card');

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
            cardAnimationActive: Boolean(loginCard?.classList.contains('auth-card--shake')),
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
                passesMinimum: gapPx === 0 || gapPx >= minimum,
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
        const desktopHeaderContentGapExpected = Number(constants.APP_PAGE_HEADER_CONTENT_GAP_EXPECTED_PX ?? 35.2);
        const mobileHeaderContentGapExpected = Number(constants.APP_PAGE_HEADER_CONTENT_GAP_MOBILE_EXPECTED_PX ?? 14.4);
        const headerContentGapTolerance = Number(constants.APP_PAGE_HEADER_CONTENT_GAP_TOLERANCE_PX ?? 2);
        const headerContentAlignmentTolerance = Number(constants.APP_PAGE_HEADER_CONTENT_ALIGNMENT_TOLERANCE_PX ?? 2);
        const alignmentTolerance = Number(constants.MOBILE_TOGGLE_CONTENT_ALIGNMENT_TOLERANCE_PX ?? 2);
        const panelHeightTolerance = Number(constants.DESKTOP_PRIMARY_PANEL_HEIGHT_TOLERANCE_PX ?? 3);
        const viewportPanelFooterGapExpected = Number(constants.DESKTOP_VIEWPORT_PANEL_FOOTER_GAP_EXPECTED_PX ?? 24);
        const viewportPanelFooterGapTolerance = Number(constants.DESKTOP_VIEWPORT_PANEL_FOOTER_GAP_TOLERANCE_PX ?? 4);
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
            expected: viewportPanelFooterGapExpected,
            tolerance: viewportPanelFooterGapTolerance,
            passesTolerance: true,
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
                    expected: desktopHeaderContentGapExpected,
                    tolerance: headerContentGapTolerance,
                    delta: null,
                    passesTolerance: true,
                },
                pageHeaderContentAlignment: {
                    present: false,
                    offsetPx: null,
                    tolerance: headerContentAlignmentTolerance,
                    passesTolerance: true,
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

        const headerContentGapExpected = isMobileViewport
            ? mobileHeaderContentGapExpected
            : desktopHeaderContentGapExpected;
        let pageHeaderContentGap = {
            present: false,
            gapPx: null,
            expected: headerContentGapExpected,
            tolerance: headerContentGapTolerance,
            delta: null,
            passesTolerance: true,
        };
        let pageHeaderContentAlignment = {
            present: false,
            offsetPx: null,
            tolerance: headerContentAlignmentTolerance,
            passesTolerance: true,
        };

        if (header && firstContentBlock) {
            // A Bootstrap row starts above its columns to balance their gutter.
            // Measure the first visible surface instead of that structural wrapper
            // so every page is compared to the Dashboard's actual first tile.
            const surfaceCandidates = Array.from(
                firstContentBlock.querySelectorAll(
                    '.metric-card, .panel-card, .cb-onboarding-wizard, .settings-tabs, .tab-content, .ssllabs-panel'
                )
            )
                .filter((element) => isVisible(element) && !isVisuallyHidden(element));
            if (firstContentBlock.matches('.metric-card, .panel-card, .cb-onboarding-wizard, .settings-tabs, .tab-content, .ssllabs-panel')) {
                surfaceCandidates.unshift(firstContentBlock);
            }
            const firstContentSurface = surfaceCandidates.reduce((topmost, candidate) =>
                !topmost || rectOf(candidate).top < rectOf(topmost).top ? candidate : topmost
                , null);
            const contentSurface = firstContentSurface || firstContentBlock;
            const headerRect = rectOf(header);
            const contentSurfaceRect = rectOf(contentSurface);
            const gapPx = Math.max(0, contentSurfaceRect.top - headerRect.bottom);
            const delta = Math.abs(gapPx - headerContentGapExpected);
            pageHeaderContentGap = {
                present: true,
                gapPx: roundTo(gapPx, 2),
                expected: headerContentGapExpected,
                tolerance: headerContentGapTolerance,
                delta: roundTo(delta, 2),
                passesTolerance: delta <= headerContentGapTolerance,
            };
            const offsetPx = contentSurfaceRect.left - headerRect.left;
            pageHeaderContentAlignment = {
                present: true,
                offsetPx: roundTo(offsetPx, 2),
                tolerance: headerContentAlignmentTolerance,
                passesTolerance: Math.abs(offsetPx) <= headerContentAlignmentTolerance,
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
            const isShown = (el) => el instanceof Element && isVisible(el) && !isVisuallyHidden(el);
            const columnSpans = Array.from(pageGrid.children)
                .filter((child) => child instanceof Element)
                .map((column) => {
                    const directPanels = Array.from(column.querySelectorAll(':scope > .panel-card')).filter(isShown);
                    // A column stacking several cards (About's Updates + Documentation)
                    // aligns as one block: first card's top to last card's bottom.
                    if (directPanels.length > 1) {
                        const first = rectOf(directPanels[0]);
                        const last = rectOf(directPanels[directPanels.length - 1]);
                        return last.bottom - first.top;
                    }
                    const panel = directPanels[0] || Array.from(column.children).find(isShown);
                    return panel ? rectOf(panel).height : null;
                })
                .filter((height) => height !== null);

            if (columnSpans.length >= 2) {
                const heights = columnSpans.slice(0, 2).map((height) => roundTo(height, 2));
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
                expected: viewportPanelFooterGapExpected,
                tolerance: viewportPanelFooterGapTolerance,
                passesTolerance: Math.abs(gapPx - viewportPanelFooterGapExpected) <= viewportPanelFooterGapTolerance,
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
            pageHeaderContentAlignment,
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

    // Sites and SSL Labs share one row rhythm: ordinary single-line rows of
    // roughly 44-48px. The median is held to the target so a handful of
    // legitimately taller rows (errors, differing endpoint grades, wrapped
    // domains) do not fail the page; single rows past the maximum only warn.
    // Card layouts are content-driven and therefore skipped.
    function managementTableDensityAnalyzer(constants = {}, scope = '') {
        const maximumRowHeight = Number(constants.MANAGEMENT_TABLE_ROW_MAX_HEIGHT_PX ?? 72);
        const targetRowHeight = Number(constants.MANAGEMENT_TABLE_ROW_TARGET_PX ?? 48);
        const tolerance = Number(constants.MANAGEMENT_TABLE_ROW_TOLERANCE_PX ?? 2);
        const isDesktopViewport = window.innerWidth >= Number(constants.LG_BREAKPOINT_PX ?? 992);
        const hasFinePointer = window.matchMedia('(hover: hover) and (pointer: fine)').matches;
        const fallback = {
            present: false,
            maximumRowHeight,
            targetRowHeight,
            tolerance,
            rowCount: 0,
            medianRowHeightPx: null,
            maxRowHeightPx: null,
            passesTarget: true,
            oversizedRows: [],
        };

        if (!['sites', 'ssllabs'].includes(scope) || !isDesktopViewport || !hasFinePointer) {
            return { managementTableDensity: fallback };
        }

        const table = document.querySelector('table.table--management');
        if (!(table instanceof Element) || !isVisible(table) || isVisuallyHidden(table)) {
            return { managementTableDensity: fallback };
        }

        const rows = Array.from(table.querySelectorAll('tbody tr'))
            .filter((row) => row instanceof Element && isVisible(row) && !isVisuallyHidden(row))
            .filter((row) => styleOf(row)?.display === 'table-row')
            // Placeholder rows (the "no sites configured" empty state) span every
            // column and are intentionally roomy: they carry no row density.
            .filter((row) => !row.querySelector('td[colspan]:not([rowspan]), th[colspan]'));
        if (!rows.length) {
            return { managementTableDensity: { ...fallback, present: true } };
        }

        const measured = rows
            .map((row, index) => ({
                index,
                text: compactText(row.textContent),
                height: rectOf(row).height,
            }))
            .filter((row) => Number.isFinite(row.height) && row.height > 0);
        if (!measured.length) {
            return { managementTableDensity: { ...fallback, present: true } };
        }

        const rowHeights = measured.map((row) => row.height).sort((a, b) => a - b);
        const middle = Math.floor(rowHeights.length / 2);
        const medianRowHeightPx = rowHeights.length % 2 === 0
            ? (rowHeights[middle - 1] + rowHeights[middle]) / 2
            : rowHeights[middle];
        const oversizedRows = measured
            .filter((row) => row.height > maximumRowHeight)
            .slice(0, 20)
            .map((row) => ({ index: row.index, text: row.text, height: roundTo(row.height, 2) }));

        return {
            managementTableDensity: {
                present: true,
                maximumRowHeight,
                targetRowHeight,
                tolerance,
                rowCount: measured.length,
                medianRowHeightPx: roundTo(medianRowHeightPx, 2),
                maxRowHeightPx: roundTo(rowHeights[rowHeights.length - 1], 2),
                passesTarget: medianRowHeightPx <= targetRowHeight + tolerance,
                oversizedRows,
            },
        };
    }

    // Shared table sizing contract: row controls and toolbar controls are
    // compact (32px / 34px) with a precise pointer on wide screens and 44px on
    // narrow or touch contexts; action spacing is 6px / 8px; read-only About
    // rows reuse the 6px block cell padding. The touch query mirrors app.css so
    // the expectation switches exactly where the stylesheet does.
    function tableRhythmAnalyzer(constants = {}, scope = '') {
        const fallback = {
            present: false,
            touchSized: false,
            controlSizeMismatches: [],
            toolbarHeightMismatches: [],
            actionGapMismatches: [],
            cellPaddingMismatches: [],
        };
        if (!['sites', 'ssllabs', 'about'].includes(scope)) {
            return { tableRhythm: fallback };
        }

        const tolerance = Number(constants.TABLE_RHYTHM_TOLERANCE_PX ?? 1);
        const touchSized = window.matchMedia(String(constants.TABLE_TOUCH_SIZING_MEDIA_QUERY || '(max-width: 1199.98px), (hover: none), (pointer: coarse)')).matches;
        const touchSize = Number(constants.CLICK_TARGET_MIN_SIZE_PX ?? 44);
        const controlSize = Number(constants.TABLE_CONTROL_SIZE_PX ?? 32);
        const toolbarSize = Number(constants.TABLE_TOOLBAR_SIZE_PX ?? 34);
        const actionGap = touchSized
            ? Number(constants.TABLE_ACTION_GAP_TOUCH_PX ?? 8)
            : Number(constants.TABLE_ACTION_GAP_PX ?? 6);
        const cellPaddingBlock = Number(constants.TABLE_CELL_PADDING_BLOCK_PX ?? 6);
        const visible = (selector) => Array.from(document.querySelectorAll(selector))
            .filter((el) => el instanceof Element && isVisible(el) && !isVisuallyHidden(el));
        const describe = (el) => ({
            tag: el.tagName,
            className: typeof el.className === 'string' ? el.className.slice(0, 120) : '',
            text: compactText(el.textContent || el.getAttribute('aria-label')),
        });
        const off = (actual, expected) => Math.abs(actual - expected) > tolerance;

        const controlSizeMismatches = visible('table.table--management .btn-sm.btn--icon-only, table.table--management select.form-select-sm')
            .map((el) => ({ el, rect: rectOf(el) }))
            .filter(({ rect }) => (touchSized
                ? rect.height < touchSize - tolerance
                : off(rect.height, controlSize)))
            .slice(0, 20)
            .map(({ el, rect }) => ({
                ...describe(el),
                height: roundTo(rect.height, 2),
                expected: touchSized ? touchSize : controlSize,
            }));

        const toolbarHeightMismatches = visible([
            '.sites-search__input',
            '.ssllabs-filterbar .form-control',
            '.ssllabs-filterbar .form-select',
            '.ssllabs-filterbar [data-ssllabs-clear-filters]',
            '.ssllabs-filterbar__quick-filters',
        ].join(', '))
            .map((el) => ({ el, rect: rectOf(el) }))
            .filter(({ rect }) => off(rect.height, touchSized ? touchSize : toolbarSize))
            .slice(0, 20)
            .map(({ el, rect }) => ({
                ...describe(el),
                height: roundTo(rect.height, 2),
                expected: touchSized ? touchSize : toolbarSize,
            }));

        const actionGapMismatches = visible('table.table--management .cell-actions, table.table--management .ssllabs-domain-card__quick-actions')
            .filter((el) => el.children.length > 1)
            .map((el) => ({ el, gap: parseFloat(styleOf(el)?.columnGap) }))
            .filter(({ gap }) => Number.isFinite(gap) && off(gap, actionGap))
            .slice(0, 20)
            .map(({ el, gap }) => ({ ...describe(el), gap: roundTo(gap, 2), expected: actionGap }));

        const cellPaddingMismatches = visible('.about-deps-table tbody td, .about-meta-table td')
            .map((el) => {
                const style = styleOf(el);
                return { el, top: parseFloat(style?.paddingTop), bottom: parseFloat(style?.paddingBottom) };
            })
            .filter(({ top, bottom }) => off(top, cellPaddingBlock) || off(bottom, cellPaddingBlock))
            .slice(0, 20)
            .map(({ el, top, bottom }) => ({
                ...describe(el),
                paddingTop: roundTo(top, 2),
                paddingBottom: roundTo(bottom, 2),
                expected: cellPaddingBlock,
            }));

        return {
            tableRhythm: {
                present: true,
                touchSized,
                controlSizeMismatches,
                toolbarHeightMismatches,
                actionGapMismatches,
                cellPaddingMismatches,
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
                if (style.display !== 'block' && style.display !== 'grid') {
                    reasons.push('notBlockOrGrid');
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
                const host = compactText(row.textContent);
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
        const alignmentTolerance = Number(constants.MOBILE_CARD_HEADING_ALIGNMENT_TOLERANCE_PX ?? 8);

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
        const edgeAlignmentScope = document.querySelector('.app-page--sites, .ssllabs-page');
        // Skip informational callouts (e.g. .ssllabs-callout): they intentionally
        // keep their card padding at mobile widths, unlike the primary content
        // panel below them (.ssllabs-panel / .sites-list-panel), which is
        // flattened flush with the page header.
        const panelCard = edgeAlignmentScope?.querySelector('.panel-card:not(.ssllabs-callout)');
        if (
            header instanceof Element
            && isVisible(header)
            && edgeAlignmentScope instanceof Element
            && panelCard instanceof Element
            && isVisible(panelCard)
        ) {
            const headerRect = rectOf(header);
            const headerLeft = roundTo(headerRect.left, 2);
            const headerRight = roundTo(headerRect.right, 2);
            const cardRect = rectOf(panelCard);
            const cardStyle = styleOf(panelCard);
            const cardPaddingLeft = Number.parseFloat(cardStyle?.paddingLeft || '0');
            const cardPaddingRight = Number.parseFloat(cardStyle?.paddingRight || '0');
            const cardContentLeft = roundTo(cardRect.left + cardPaddingLeft, 2);
            const cardContentRight = roundTo(cardRect.right - cardPaddingRight, 2);
            const leftDelta = roundTo(Math.abs(headerLeft - cardContentLeft), 2);
            const rightDelta = roundTo(Math.abs(headerRight - cardContentRight), 2);
            mobileCardEdgeAlignment = {
                present: true,
                headerLeft,
                cardContentLeft,
                leftDelta,
                tolerance: alignmentTolerance,
                matchesLeft: leftDelta <= alignmentTolerance,
                headerRight,
                cardContentRight,
                rightDelta,
                matchesRight: rightDelta <= alignmentTolerance,
            };
        }

        return {
            mobileTopbarClearance,
            mobileCardEdgeAlignment,
        };
    }

    // The page gradient lives on <html>; any opaque body background (e.g. the
    // Bootstrap reboot's --bs-body-bg) silently covers it.
    function pageBackdropAnalyzer() {
        const htmlStyle = styleOf(document.documentElement);
        const bodyStyle = document.body ? styleOf(document.body) : null;
        const htmlBackgroundImage = htmlStyle?.backgroundImage || 'none';
        const bodyBackgroundColor = bodyStyle?.backgroundColor || '';
        const bodyBackgroundImage = bodyStyle?.backgroundImage || 'none';
        const bodyAlpha = (() => {
            const match = bodyBackgroundColor.match(/rgba?\(([^)]+)\)/);
            if (!match) return bodyBackgroundColor === 'transparent' ? 0 : 1;
            const parts = match[1].split(/[\s,/]+/).filter(Boolean);
            return parts.length >= 4 ? Number(parts[3]) : 1;
        })();
        const hasRootGradient = htmlBackgroundImage !== 'none';
        const bodyCoversRoot = bodyAlpha > 0 || bodyBackgroundImage !== 'none';
        return {
            pageBackdrop: {
                present: Boolean(bodyStyle),
                htmlHasGradient: hasRootGradient,
                bodyBackgroundColor,
                bodyHasImage: bodyBackgroundImage !== 'none',
                passesBackdrop: hasRootGradient && !bodyCoversRoot,
            },
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
        const pageBackdrop = pageBackdropAnalyzer();
        const primaryPanelPadding = primaryPanelPaddingAnalyzer(constants);
        const pageStructure = pageStructureAnalyzer();
        const sitesFormControlHeights = sitesFormControlHeightAnalyzer(constants, scope);
        const managementTableDensity = managementTableDensityAnalyzer(constants, scope);
        const tableRhythm = tableRhythmAnalyzer(constants, scope);
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
            ...pageBackdrop,
            ...primaryPanelPadding,
            ...pageStructure,
            ...sitesFormControlHeights,
            ...managementTableDensity,
            ...tableRhythm,
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
            loginFailure: scope === 'login' ? loginFailureAnalyzer(selectors.loginFailureAlert || []) : null,
        };
    }

    // ---------------- Preference probes ----------------
    // Not part of runAll(): audits run under prefers-reduced-motion: reduce, so
    // collectPreferenceProbes (lib/browser-utils.mjs) switches the media feature
    // each probe needs, calls it, and restores the audit state afterwards.

    // A toast at rest (not .is-entered) must sit entirely outside the viewport,
    // or its slide starts and ends with a visible sliver. Probes a throwaway
    // stack so it does not depend on a flash message being on screen.
    function toastExitProbe(constants = {}) {
        if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
            return { present: false, offenders: [] };
        }
        const count = Math.max(1, numberConstant(constants.TOAST_EXIT_PROBE_COUNT, 3));
        const stack = document.createElement('div');
        // Keep in sync with templates/partials/flashes.html.
        stack.className = 'toast-container app-toast-stack position-fixed bottom-0 end-0 p-3';
        stack.setAttribute('aria-hidden', 'true');
        const toasts = Array.from({ length: count }, (_, index) => {
            const toast = document.createElement('div');
            toast.className = 'toast toast-slide show';
            toast.style.transition = 'none';
            const body = document.createElement('div');
            body.className = 'toast-body';
            body.textContent = `UI lint toast probe ${index + 1}`;
            toast.append(body);
            stack.append(toast);
            return toast;
        });
        document.body.append(stack);
        try {
            const setExitOffset = window.CaddyBuddyApp?.setToastExitOffset;
            if (typeof setExitOffset === 'function') {
                toasts.forEach((toast) => setExitOffset(toast));
            }
            const offenders = toasts
                .map((toast, index) => {
                    const rect = toast.getBoundingClientRect();
                    const visibleWidth = Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0);
                    const visibleHeight = Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0);
                    return { index, rect, visibleWidth, visibleHeight };
                })
                .filter(({ visibleWidth, visibleHeight }) => visibleWidth > 0 && visibleHeight > 0)
                .map(({ index, rect, visibleWidth, visibleHeight }) => ({
                    index,
                    stackSize: count,
                    visibleWidth: roundTo(visibleWidth, 2),
                    visibleHeight: roundTo(visibleHeight, 2),
                    left: roundTo(rect.left, 2),
                    top: roundTo(rect.top, 2),
                    viewportWidth: window.innerWidth,
                    viewportHeight: window.innerHeight,
                }));
            return { present: true, usesExitOffsetHook: typeof setExitOffset === 'function', offenders };
        } finally {
            stack.remove();
        }
    }

    // Under prefers-reduced-transparency: reduce no element may keep a
    // backdrop-filter. Hidden elements count too: an overlay such as
    // .sidebar-backdrop is invisible at audit time but blurs once opened.
    function reducedTransparencyProbe() {
        if (!window.matchMedia('(prefers-reduced-transparency: reduce)').matches) {
            return { present: false, offenders: [] };
        }
        const offenders = Array.from(document.querySelectorAll('body *'))
            .map((el) => ({ el, backdropFilter: styleOf(el)?.backdropFilter || 'none' }))
            .filter(({ backdropFilter }) => backdropFilter !== 'none')
            .slice(0, 20)
            .map(({ el, backdropFilter }) => ({
                tag: el.tagName,
                id: el.id || '',
                className: typeof el.className === 'string' ? el.className : '',
                backdropFilter,
            }));
        return { present: true, offenders };
    }

    window.__uiLint = { runAll, toastExitProbe, reducedTransparencyProbe };
})();

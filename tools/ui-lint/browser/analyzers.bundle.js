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

    function pushStyleMismatch(issues, { el, rule, selector, index, prop, expected }) {
        const actual = styleOf(el)?.[prop];
        if (actual !== expected) {
            issues.push({ rule, selector, index, value: actual, expected });
        }
    }

    function isModalActive(el) {
        if (!el) return false;
        return el.classList.contains('is-open') || el.matches('.modal.show') || styleOf(el)?.display === 'flex';
    }

    function getOpenModalElements() {
        return Array.from(document.querySelectorAll(OPEN_MODAL_CANDIDATE_SELECTOR))
            .filter(isModalActive);
    }

    function getOpenModalOverlay() {
        return Array.from(document.querySelectorAll(OPEN_MODAL_OVERLAY_SELECTOR))
            .find(isModalActive) || null;
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

        const wrappingLabelText = el.closest('label')?.textContent?.trim() || '';
        if (wrappingLabelText) return wrappingLabelText;

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

        function isFocusIndicatorMissing(el) {
            const previousActive = document.activeElement instanceof HTMLElement ? document.activeElement : null;
            const controller = new AbortController();

            // Suppress event propagation to prevent application state mutation
            const listeners = (event) => event.stopImmediatePropagation();
            const target = el.closest('form') || el.closest('[tabindex]') || document;
            const eventTypes = ['focusin', 'focus', 'focusout', 'blur'];
            for (const type of eventTypes) {
                target.addEventListener(type, listeners, { capture: true, signal: controller.signal });
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
            .filter((el) => el.hasAttribute('aria-label') && !String(el.getAttribute('aria-label') || '').trim())
            .map((el) => ({ tag: el.tagName }));

        let focusIndicatorMissing = [];
        if (constants.ENABLE_FOCUS_LINTING) {
            focusIndicatorMissing = interactive
                .filter(isVisible)
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
            .filter(isVisible)
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
        const isAuditLogsScope = scope === 'audit-logs';
        const isAllowedAuditLogsScroller = (el) => {
            if (!isAuditLogsScope || !(el instanceof Element)) {
                return false;
            }
            return el.matches('.audit-logs-stream, #auditTableContainer, .audit-row__details');
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
            .filter((el) => !isAllowedAuditLogsScroller(el));

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
    function interactionAnalyzer(constants, selectors) {
        const targets = Array.from(document.querySelectorAll(selectors.clickTarget || 'button'));
        const minSize = Number(constants.CLICK_TARGET_MIN_SIZE_PX);

        const tooSmall = targets
            .filter(isVisible)
            .filter((el) => !isVisuallyHidden(el))
            .map((el) => ({ el, rect: rectOf(el) }))
            .filter(({ rect }) => rect.width < minSize || rect.height < minSize)
            .slice(0, 20)
            .map(({ el, rect }) => ({
                tag: el.tagName,
                width: rect.width,
                height: rect.height,
            }));

        const buttonAlignmentIssues = Array.from(document.querySelectorAll('.btn:not(.btn-close):not(input)'))
            .filter(isVisible)
            .map((el) => {
                const style = styleOf(el);
                const display = style?.display || '';
                const hasFlexDisplay = display === 'flex' || display === 'inline-flex';
                if (!hasFlexDisplay) return null;

                const alignItems = style?.alignItems || '';
                const justifyContent = style?.justifyContent || '';
                if (alignItems === 'center' && justifyContent === 'center') return null;

                return {
                    tag: el.tagName,
                    classes: el.className || '',
                    display,
                    alignItems,
                    justifyContent,
                    text: (el.textContent || '').trim().slice(0, 80),
                };
            })
            .filter(Boolean)
            .slice(0, 20);

        return {
            clickTargetsTooSmall: tooSmall,
            buttonAlignmentIssues,
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

            const consoleBgRgb = parseRgb(style.backgroundImage)
                || parseRgb(style.backgroundColor);
            const consoleFgRgb = parseRgb(lineStyle?.color || style.color);
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

        const loadingWithoutDisabled = buttons
            .filter((btn) => /loading|saving|creating|deleting|processing/i.test((btn.textContent || '').trim()) && !btn.disabled)
            .map((btn) => ({ text: (btn.textContent || '').trim() }));

        const missingAriaBusy = buttons
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
        const openModal = getOpenModalOverlay();
        if (theme !== 'dark' || !openModal || !isVisible(openModal)) {
            return { modalThemeIssues: [] };
        }

        const dialog = openModal.querySelector('.modal');
        if (!dialog || !isVisible(dialog)) {
            return { modalThemeIssues: [] };
        }

        const dialogStyle = styleOf(dialog);
        const dialogBg = parseRgb(dialogStyle.backgroundColor);
        const dialogLuma = relativeLuminance(dialogBg);
        const modalDialogMaxLuma = Number(constants.MODAL_DARK_DIALOG_MAX_LUMA ?? 0.3);
        const modalControlLightBgMinLuma = Number(constants.MODAL_CONTROL_LIGHT_BG_MIN_LUMA ?? 0.72);
        const modalControlDarkTextMaxLuma = Number(constants.MODAL_CONTROL_DARK_TEXT_MAX_LUMA ?? 0.3);
        const controls = Array.from(dialog.querySelectorAll('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), textarea, select'));

        const issues = controls
            .filter(isVisible)
            .map((control) => {
                const style = styleOf(control);
                const bg = parseRgb(style.backgroundColor);
                const fg = parseRgb(style.color);
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
        const rgb = parseRgb(colorValue);
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

    function mobileSidebarFooterAnalyzer(constants = {}) {
        const isMobileViewport = window.innerWidth < Number(constants.LG_BREAKPOINT_PX ?? 992);
        const sidebar = document.querySelector('.app-sidebar');
        const sidebarFooter = document.querySelector('.sidebar-footer');
        const minimum = Number(constants.SIDEBAR_FOOTER_VIEWPORT_CLEARANCE_MIN_PX ?? 0);

        if (!isMobileViewport || !sidebar || !sidebarFooter || !isVisible(sidebar) || !isVisible(sidebarFooter)) {
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

    function runAll({ scope, constants = {}, selectors = {} } = {}) {
        resetRunCache();

        const accessibility = accessibilityAnalyzer(constants);
        const layout = layoutAnalyzer(constants);
        const scrollContainment = scrollContainmentAnalyzer(constants, scope);
        const interaction = interactionAnalyzer(constants, selectors);
        const contrast = contrastAnalyzer(constants);
        const footerGap = footerGapAnalyzer(constants);
        const sidebarFooterGap = mobileSidebarFooterAnalyzer(constants);
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
            ...modalTheme,

            state,
            components,
            tokens,
            loginFailure: scope === 'login' ? loginFailureAnalyzer() : null,
        };
    }

    window.__uiLint = { runAll };
})();

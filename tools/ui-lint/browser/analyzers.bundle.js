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
    function accessibilityAnalyzer() {
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

            try {
                el.focus({ preventScroll: true });
            } catch {
                return true;
            }

            resetRunCache();

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

            resetRunCache();
            return missing;
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

        const focusIndicatorMissing = interactive
            .filter(isVisible)
            .filter((el) => isFocusIndicatorMissing(el))
            .slice(0, 20)
            .map((el) => ({ tag: el.tagName }));

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

        return {
            horizontalOverflow: {
                hasOverflow: doc.scrollWidth > window.innerWidth + (constants.OVERFLOW_TOLERANCE_PX || 0),
            },
        };
    }

    // ---------------- Interaction ----------------
    function interactionAnalyzer(constants, selectors) {
        const targets = Array.from(document.querySelectorAll(selectors.clickTarget || 'button'));
        const minSize = Number(constants.CLICK_TARGET_MIN_SIZE_PX);

        const tooSmall = targets
            .filter(isVisible)
            .map((el) => ({ el, rect: rectOf(el) }))
            .filter(({ rect }) => rect.width < minSize || rect.height < minSize)
            .slice(0, 20)
            .map(({ el, rect }) => ({
                tag: el.tagName,
                width: rect.width,
                height: rect.height,
            }));

        return { clickTargetsTooSmall: tooSmall };
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
        const banner = document.getElementById('login-error-banner');
        const bannerTextEl = document.getElementById('login-error-banner-text');
        const submitBtn = document.getElementById('submit-btn');
        const passwordInput = document.getElementById('password');
        const loginCard = document.getElementById('login-card');

        const bannerVisible = Boolean(
            banner
            && isVisible(banner)
            && banner.getAttribute('aria-hidden') !== 'true'
            && (banner.classList.contains('is-visible') || banner.style.visibility !== 'hidden')
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

    function runAll({ scope, constants = {}, selectors = {} } = {}) {
        resetRunCache();

        const accessibility = accessibilityAnalyzer();
        const layout = layoutAnalyzer(constants);
        const interaction = interactionAnalyzer(constants, selectors);
        const contrast = contrastAnalyzer(constants);
        const footerGap = footerGapAnalyzer(constants);
        const state = stateAnalyzer();
        const components = componentAnalyzer();
        const modalTheme = modalThemeAnalyzer(constants);
        const tokens = tokenAnalyzer();

        return {
            // Preserve the mixed nested + flat metrics contract for existing findings consumers.
            accessibility,
            layout,
            interaction,
            contrast,

            // Expose flattened keys expected by findings.
            ...accessibility,
            ...interaction,
            ...contrast,
            ...footerGap,
            ...modalTheme,

            state,
            components,
            tokens,
            loginFailure: scope === 'login' ? loginFailureAnalyzer() : null,
        };
    }

    window.__uiLint = { runAll };
})();

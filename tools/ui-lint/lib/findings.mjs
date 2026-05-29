//
// tools/ui-lint/lib/findings.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import {
    DESKTOP_TABLE_CELL_MIN_FONT_SIZE_PX,
    DESKTOP_TABLE_HEAD_MIN_FONT_SIZE_PX,
    KPI_CARD_PADDING_EXPECTED,
    KPI_CARD_PADDING_TOLERANCE,
    KPI_CARD_REQUIRED_SCOPES,
    KPI_HEIGHT_TOLERANCE_PX,
    KPI_ICON_MAX,
    KPI_ICON_MIN,
    KPI_ROW_VARIANCE_MAX,
    LAYOUT_SHIFT_THRESHOLD,
    APP_PAGE_HEADER_CONTENT_GAP_MAX_PX,
    MOBILE_TOGGLE_CONTENT_ALIGNMENT_TOLERANCE_PX,
    DESKTOP_PRIMARY_PANEL_HEIGHT_TOLERANCE_PX,
    DESKTOP_VIEWPORT_PANEL_FOOTER_GAP_MAX_PX,
    VISUAL_DRIFT_THRESHOLD,
} from './constants.mjs';


const CARD_HEADER_PADDING_TOP_MAX_PX = 12;
const CARD_HEADER_PADDING_LEFT_MAX_PX = 20;
const DUPLICATE_REQUEST_THRESHOLD = 3;
const EVENT_STREAM_PATH = '/api/v1/events';
const LAYOUT_SHIFT_UNSUPPORTED_MESSAGE = 'Ignoring unsupported entryTypes: layout-shift.';
const INLINE_STYLE_CSP_MESSAGE = "Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear in the style-src directive of the Content Security Policy.";


function detectViewContext(name) {
    const normalizedName = String(name || '');
    return {
        name: normalizedName,
        isMobile: normalizedName.includes('mobile-') || normalizedName.includes('mobile'),
        isDesktop: normalizedName.includes('desktop-'),
        isLoginError: normalizedName.includes('login-error'),
    };
}


function normalizeRequestUrl(url) {
    try {
        const parsed = new URL(url);
        parsed.search = '';
        return `${parsed.origin}${parsed.pathname}`;
    } catch {
        return String(url || '');
    }
}

function isKnownAuditConsoleNoise(entry, viewName) {
    const text = String(entry?.text || '');
    if (text.includes(LAYOUT_SHIFT_UNSUPPORTED_MESSAGE)) {
        return true;
    }
    if (String(viewName || '').startsWith('webkit-') && text === INLINE_STYLE_CSP_MESSAGE) {
        return true;
    }
    return false;
}

function isExpectedRequestFailure(entry) {
    const url = normalizeRequestUrl(entry?.url);
    const error = String(entry?.error || '').toLowerCase();
    if (!url.endsWith(EVENT_STREAM_PATH)) {
        return false;
    }
    return error.includes('cancel') || error.includes('aborted') || error.includes('err_aborted');
}

/**
 * Aggregate raw analyzer output into categorized hard findings and warnings.
 */
export function summarizeFindings(result) {
    const source = result && typeof result === 'object' ? result : {};
    const sourceMetrics = source.metrics && typeof source.metrics === 'object' ? source.metrics : {};
    const report = {
        ...source,
        metrics: {
            ...sourceMetrics,
            state: sourceMetrics.state && typeof sourceMetrics.state === 'object' ? { ...sourceMetrics.state } : sourceMetrics.state,
            horizontalOverflow: sourceMetrics.horizontalOverflow && typeof sourceMetrics.horizontalOverflow === 'object'
                ? { ...sourceMetrics.horizontalOverflow }
                : sourceMetrics.horizontalOverflow,
            cardContainment: sourceMetrics.cardContainment && typeof sourceMetrics.cardContainment === 'object'
                ? { ...sourceMetrics.cardContainment }
                : sourceMetrics.cardContainment,
        },
        diff: source.diff && typeof source.diff === 'object' ? { ...source.diff } : source.diff,
        network: source.network && typeof source.network === 'object' ? { ...source.network } : source.network,
    };
    const { name, isMobile, isDesktop, isLoginError } = detectViewContext(report.name);

    const metrics = report.metrics && typeof report.metrics === 'object' ? report.metrics : {};
    report.metrics = metrics;

    const ensureArray = (obj, key) => {
        if (!Array.isArray(obj[key])) obj[key] = [];
    };
    const ensureObject = (obj, key, fallback = {}) => {
        if (!obj[key] || typeof obj[key] !== 'object') obj[key] = fallback;
    };

    [
        'duplicateIds',
        'emptyAriaLabels',
        'unlabeledControls',
        'namelessButtons',
        'headingSkips',
        'tablesWithoutHeaders',
        'clippedButtons',
        'hiddenInteractiveElements',
        'viewportClippedInteractiveElements',
        'contrastProblems',
    ].forEach((key) => ensureArray(metrics, key));

    ensureObject(metrics, 'spacing', {});
    ensureObject(metrics, 'horizontalOverflow', { hasOverflow: false, offenders: [] });
    ensureObject(metrics, 'cardContainment', { cardsPastFooter: [] });
    ensureObject(metrics, 'footerViewportGap', { present: false, gapPx: null, minimum: 0, passesMinimum: true });
    ensureObject(metrics, 'sidebarFooterViewportGap', { present: false, gapPx: null, minimum: 0, passesMinimum: true, requiresScroll: false });
    ensureObject(metrics, 'sidebarNavSpacing', {
        present: false,
        navGapPx: null,
        linkMinHeightPx: null,
        minimumGap: 0,
        minimumLinkHeight: 0,
        passesGap: true,
        passesLinkHeight: true,
    });
    ensureObject(metrics, 'sitesFormControlHeights', {
        present: false,
        expectedHeight: 0,
        tolerance: 0,
        siteNameHeightPx: null,
        domainControlHeightPx: null,
        passesSiteName: true,
        passesDomainControl: true,
    });
    ensureObject(metrics, 'appPageLayout', { present: false, overflowY: null, locksVerticalOverflow: false });
    ensureObject(metrics, 'mobileToggleContentAlignment', {
        present: false,
        toggleLeft: null,
        contentLeft: null,
        delta: null,
        tolerance: MOBILE_TOGGLE_CONTENT_ALIGNMENT_TOLERANCE_PX,
        passesTolerance: true,
    });
    ensureObject(metrics, 'desktopPrimaryPanelHeightAlignment', {
        present: false,
        heights: [],
        delta: null,
        tolerance: DESKTOP_PRIMARY_PANEL_HEIGHT_TOLERANCE_PX,
        passesTolerance: true,
    });
    ensureObject(metrics, 'desktopViewportPanelFooterGap', {
        present: false,
        gapPx: null,
        maximum: DESKTOP_VIEWPORT_PANEL_FOOTER_GAP_MAX_PX,
        passesMaximum: true,
    });
    ensureObject(metrics, 'primaryPanelPadding', { present: false, tolerance: 0, panels: [], mismatches: [] });
    ensureObject(metrics, 'pageHeaderContentGap', {
        present: false,
        gapPx: null,
        maximum: APP_PAGE_HEADER_CONTENT_GAP_MAX_PX,
        passesMaximum: true,
    });
    ensureObject(metrics, 'layoutShift', { value: 0 });
    ensureObject(metrics, 'state', { loadingWithoutDisabled: [], missingAriaBusy: [] });
    ensureObject(metrics, 'caddyfileValidationGuard', { present: false, emptyStateAllowsValidation: false });
    ensureObject(metrics, 'components', {
        modal: { multipleOpen: false, count: 0 },
        toast: { count: 0, stackingIssue: false },
    });
    ensureObject(metrics, 'tokens', { hardcodedStyles: 0 });
    ensureObject(metrics, 'loginFailure', {});
    ensureArray(metrics.state, 'loadingWithoutDisabled');
    ensureArray(metrics.state, 'missingAriaBusy');
    ensureArray(metrics.horizontalOverflow, 'offenders');
    ensureArray(metrics.cardContainment, 'cardsPastFooter');

    ensureObject(report, 'diff', { ratio: 0, sizeMismatch: false });
    ensureObject(report, 'network', {
        consoleEntries: [],
        pageErrors: [],
        requestFailures: [],
        badResponses: [],
        requests: [],
        duplicateRequests: [],
    });
    ensureArray(report.network, 'consoleEntries');
    ensureArray(report.network, 'pageErrors');
    ensureArray(report.network, 'requestFailures');
    ensureArray(report.network, 'badResponses');
    ensureArray(report.network, 'requests');
    ensureArray(report.network, 'duplicateRequests');

    const hardFindings = [];
    const warnings = [];
    const pushHard = (value) => hardFindings.push(value);
    const pushWarning = (value) => warnings.push(value);

    if (metrics.duplicateIds.length) pushHard(`duplicateIds=${metrics.duplicateIds.length}`);
    if (report.visualRegression) {
        if (report.visualRegression.pass === false) {
            pushHard(`visualRegression=${report.visualRegression.reason}`);
        } else if (report.visualRegression.reason === 'baseline-created') {
            pushWarning('visualBaselineCreated');
        }
    }

    if (metrics.emptyAriaLabels.length) pushWarning(`emptyAriaLabels=${metrics.emptyAriaLabels.length}`);
    if (metrics.unlabeledControls.length) pushHard(`unlabeledControls=${metrics.unlabeledControls.length}`);
    if (metrics.namelessButtons.length) pushHard(`namelessButtons=${metrics.namelessButtons.length}`);
    if (metrics.headingSkips.length) pushWarning(`headingSkips=${metrics.headingSkips.length}`);
    if (metrics.tablesWithoutHeaders.length) pushWarning(`tablesWithoutHeaders=${metrics.tablesWithoutHeaders.length}`);
    if (metrics.tableCellOverlapIssues?.length) pushHard(`tableCellOverlaps=${metrics.tableCellOverlapIssues.length}`);
    if (metrics.tablesWithoutResponsive?.length) pushWarning(`tablesWithoutResponsive=${metrics.tablesWithoutResponsive.length}`);

    if (isDesktop && metrics.spacing.desktopTableTypography) {
        const tableTypography = metrics.spacing.desktopTableTypography;
        if (tableTypography.headFontSizePass === false) {
            pushWarning(`desktopTableHeadFont=${tableTypography.headFontSize}/${DESKTOP_TABLE_HEAD_MIN_FONT_SIZE_PX}`);
        }
        if (tableTypography.bodyFontSizePass === false) {
            pushWarning(`desktopTableCellFont=${tableTypography.bodyFontSize}/${DESKTOP_TABLE_CELL_MIN_FONT_SIZE_PX}`);
        }
    }

    if (metrics.ghostScroll) pushWarning('ghostScrollDetected');
    if (metrics.ghostScrollContainers?.length) pushWarning(`ghostScrollContainers=${metrics.ghostScrollContainers.length}`);
    if (metrics.horizontalOverflow.hasOverflow) pushHard('horizontalOverflow');
    if (metrics.horizontalOverflow.hasOverflow && metrics.horizontalOverflow.offenders.length) {
        pushHard(`overflowOffenders=${metrics.horizontalOverflow.offenders.length}`);
    }
    if (metrics.clippedButtons.length) pushWarning(`clippedButtons=${metrics.clippedButtons.length}`);
    if (metrics.ssllabsPrematureDesktopLayoutIssues?.length) {
        pushHard(`ssllabsPrematureDesktopLayout=${metrics.ssllabsPrematureDesktopLayoutIssues.length}`);
    }
    if (metrics.buttonAlignmentIssues?.length) pushHard(`buttonAlignmentIssues=${metrics.buttonAlignmentIssues.length}`);
    if (metrics.badgeAlignmentIssues?.length) pushHard(`badgeAlignmentIssues=${metrics.badgeAlignmentIssues.length}`);
    if (metrics.clickTargetsTooSmall?.length) pushHard(`clickTargetsTooSmall=${metrics.clickTargetsTooSmall.length}`);
    if (metrics.viewportClippedInteractiveElements.length) {
        pushHard(`viewportClippedInteractive=${metrics.viewportClippedInteractiveElements.length}`);
    }
    if (metrics.hiddenInteractiveElements.length) pushHard(`hiddenInteractive=${metrics.hiddenInteractiveElements.length}`);
    if (metrics.bootstrapGridIssues?.length) pushWarning(`bootstrapGridIssues=${metrics.bootstrapGridIssues.length}`);
    if (metrics.bootstrapColumnsOutsideRows?.length) pushWarning(`bootstrapColumnsOutsideRows=${metrics.bootstrapColumnsOutsideRows.length}`);
    if (metrics.breakpointDisplayConflicts?.length) pushWarning(`breakpointDisplayConflicts=${metrics.breakpointDisplayConflicts.length}`);
    if (metrics.navbarCollapseIssues?.length) pushWarning(`navbarCollapseIssues=${metrics.navbarCollapseIssues.length}`);
    if (metrics.focusOrderIssues?.length) pushWarning(`focusOrderIssues=${metrics.focusOrderIssues.length}`);
    if (metrics.focusIndicatorMissing?.length) pushWarning(`focusIndicatorMissing=${metrics.focusIndicatorMissing.length}`);
    if (metrics.state.loadingWithoutDisabled.length) pushHard(`loadingWithoutDisabled=${metrics.state.loadingWithoutDisabled.length}`);
    if (metrics.state.missingAriaBusy.length) pushWarning(`missingAriaBusy=${metrics.state.missingAriaBusy.length}`);
    if (metrics.caddyfileValidationGuard.present && metrics.caddyfileValidationGuard.emptyStateAllowsValidation) {
        pushHard('caddyfileEmptyValidateEnabled');
    }
    if (metrics.components.modal.multipleOpen) pushHard(`multipleModalsOpen=${metrics.components.modal.count}`);
    if (metrics.modalThemeIssues?.length) pushHard(`modalThemeIssues=${metrics.modalThemeIssues.length}`);
    if (metrics.components.toast.stackingIssue) pushWarning(`toastStacking=${metrics.components.toast.count}`);
    if ((metrics.tokens.hardcodedStyles || 0) > 0) pushWarning(`hardcodedStyles=${metrics.tokens.hardcodedStyles}`);
    if (metrics.scrollEdgeCrowding?.length) pushWarning(`scrollEdgeCrowding=${metrics.scrollEdgeCrowding.length}`);
    if (metrics.scrollBottomCrowding?.length) pushWarning(`scrollBottomCrowding=${metrics.scrollBottomCrowding.length}`);
    if (metrics.nestedScrollContainers?.length) pushWarning(`nestedScrollContainers=${metrics.nestedScrollContainers.length}`);

    if (metrics.flexScrollTraps?.length) {
        const message = `flexScrollTraps=${metrics.flexScrollTraps.length}`;
        if (isMobile) {
            pushHard(message);
        } else {
            pushWarning(message);
        }
    }
    if (metrics.doubleScrollRisk) {
        const message = `doubleScroll=${metrics.doubleScrollRisk.innerScrollCount}`;
        if (isMobile) {
            pushHard(message);
        } else {
            pushWarning(message);
        }
    }

    if (metrics.badgeStyleMismatches?.length) pushWarning(`badgeStyleMismatches=${metrics.badgeStyleMismatches.length}`);
    if (metrics.buttonContrastIssues?.length) pushHard(`buttonContrastIssues=${metrics.buttonContrastIssues.length}`);
    if (metrics.nonTokenColorUsage?.length) pushWarning(`nonTokenColorUsage=${metrics.nonTokenColorUsage.length}`);
    if (metrics.monospaceToneMismatches?.length) pushWarning(`monospaceToneMismatches=${metrics.monospaceToneMismatches.length}`);
    if (metrics.footerViewportGap.present && metrics.footerViewportGap.passesMinimum === false) {
        pushWarning(`footerViewportGap=${metrics.footerViewportGap.gapPx}/${metrics.footerViewportGap.minimum}`);
    }
    if (metrics.sidebarFooterViewportGap.present && metrics.sidebarFooterViewportGap.passesMinimum === false) {
        const gapLabel = metrics.sidebarFooterViewportGap.gapPx === null
            ? 'clipped'
            : metrics.sidebarFooterViewportGap.gapPx;
        pushHard(`sidebarFooterViewportGap=${gapLabel}/${metrics.sidebarFooterViewportGap.minimum}`);
    }
    if (
        metrics.sidebarNavSpacing.present
        && (metrics.sidebarNavSpacing.passesGap === false || metrics.sidebarNavSpacing.passesLinkHeight === false)
    ) {
        pushHard(
            `sidebarNavSpacing=${metrics.sidebarNavSpacing.navGapPx}/${metrics.sidebarNavSpacing.minimumGap}/${metrics.sidebarNavSpacing.linkMinHeightPx}/${metrics.sidebarNavSpacing.minimumLinkHeight}`
        );
    }
    if (
        metrics.sitesFormControlHeights.present
        && (metrics.sitesFormControlHeights.passesSiteName === false || metrics.sitesFormControlHeights.passesDomainControl === false)
    ) {
        pushHard(
            `sitesFormControlHeights=${metrics.sitesFormControlHeights.siteNameHeightPx}/${metrics.sitesFormControlHeights.domainControlHeightPx}/${metrics.sitesFormControlHeights.expectedHeight}/${metrics.sitesFormControlHeights.tolerance}`
        );
    }
    if (metrics.appPageLayout.present && metrics.appPageLayout.locksVerticalOverflow) {
        pushWarning(`appPageOverflow=${metrics.appPageLayout.overflowY}`);
    }
    if (isMobile && metrics.mobileToggleContentAlignment.present && metrics.mobileToggleContentAlignment.passesTolerance === false) {
        pushHard(`mobileToggleContentAlignment=${metrics.mobileToggleContentAlignment.delta}/${metrics.mobileToggleContentAlignment.tolerance}`);
    }
    if (isDesktop && metrics.desktopPrimaryPanelHeightAlignment.present && metrics.desktopPrimaryPanelHeightAlignment.passesTolerance === false) {
        pushHard(`desktopPrimaryPanelHeightAlignment=${metrics.desktopPrimaryPanelHeightAlignment.delta}/${metrics.desktopPrimaryPanelHeightAlignment.tolerance}`);
    }
    if (isDesktop && metrics.desktopViewportPanelFooterGap.present && metrics.desktopViewportPanelFooterGap.passesMaximum === false) {
        pushHard(`desktopViewportPanelFooterGap=${metrics.desktopViewportPanelFooterGap.gapPx}/${metrics.desktopViewportPanelFooterGap.maximum}`);
    }
    if (metrics.cardContainment.cardsPastFooter.length) pushWarning(`cardsPastFooter=${metrics.cardContainment.cardsPastFooter.length}`);
    if (metrics.primaryPanelPadding.present && metrics.primaryPanelPadding.mismatches?.length) {
        const first = metrics.primaryPanelPadding.mismatches[0];
        pushWarning(
            `primaryPanelPaddingMismatch=${first.paddingTop}/${first.paddingRight}/${first.paddingBottom}/${first.paddingLeft}`
        );
    }
    if (metrics.pageStructureConsistent?.present && metrics.pageStructureConsistent.issues?.length) {
        pushHard(`pageStructureMissingRowWrapper=${metrics.pageStructureConsistent.issues.length}`);
    }
    if (metrics.pageHeaderContentGap.present && metrics.pageHeaderContentGap.passesMaximum === false) {
        pushWarning(`pageHeaderContentGap=${metrics.pageHeaderContentGap.gapPx}/${metrics.pageHeaderContentGap.maximum}`);
    }

    if (metrics.spacing.outlierVerticalGaps?.length) pushWarning(`outlierVerticalGaps=${metrics.spacing.outlierVerticalGaps.length}`);
    if (isMobile && metrics.spacing.mobileRowCardStackGaps?.length) {
        const inconsistentRows = metrics.spacing.mobileRowCardStackGaps.filter((entry) => !entry.gapsConsistent);
        if (inconsistentRows.length) {
            pushWarning(`mobileRowCardStackGapVariance=${inconsistentRows.length}`);
        }
    }
    if (isMobile && metrics.spacing.mobileCardEdgeAlignment) {
        const edgeAlignment = metrics.spacing.mobileCardEdgeAlignment;
        if (!edgeAlignment.matchesLeft || !edgeAlignment.matchesRight) {
            pushWarning(`mobileCardEdgeAlignment=${edgeAlignment.leftDelta}/${edgeAlignment.rightDelta}`);
        }
    }
    if (metrics.spacing.cardHeaderPadding?.all_compact === false && metrics.spacing.cardHeaderPadding.samples?.length) {
        const oversized = metrics.spacing.cardHeaderPadding.samples.filter(
            (sample) => sample.top > CARD_HEADER_PADDING_TOP_MAX_PX || sample.left > CARD_HEADER_PADDING_LEFT_MAX_PX
        );
        if (oversized.length) {
            pushWarning(`cardHeaderPaddingOversized=${oversized[0].top}/${oversized[0].left}px`);
        }
    }
    if (metrics.spacing.logoShadow?.all_have_shadow === false && metrics.spacing.logoShadow.samples?.length) {
        const missing = metrics.spacing.logoShadow.samples.filter((sample) => !sample.hasDropShadow);
        if (missing.length) {
            pushWarning(`logoMissingShadow=${missing[0].element}`);
        }
    }

    if (metrics.spacing.kpiCards?.length) {
        const paddingProblems = metrics.spacing.kpiCards.filter((card) =>
            Math.abs(card.paddingTop - KPI_CARD_PADDING_EXPECTED) > KPI_CARD_PADDING_TOLERANCE ||
            Math.abs(card.paddingBottom - KPI_CARD_PADDING_EXPECTED) > KPI_CARD_PADDING_TOLERANCE
        );
        if (paddingProblems.length) pushWarning(`kpiPaddingMismatch=${paddingProblems.length}`);

        const iconProblems = metrics.spacing.kpiCards.filter((card) =>
            card.iconSize && (card.iconSize < KPI_ICON_MIN || card.iconSize > KPI_ICON_MAX)
        );
        if (iconProblems.length) pushWarning(`kpiIconSizeMismatch=${iconProblems.length}`);
    }

    if (metrics.spacing.kpiHeights?.length) {
        const heights = metrics.spacing.kpiHeights;
        const variance = metrics.spacing.kpiHeightVariance || 0;
        if (variance > KPI_ROW_VARIANCE_MAX) pushWarning(`kpiHeightVariance=${variance}`);

        const sortedHeights = [...heights].sort((a, b) => a - b);
        const medianHeight = sortedHeights[Math.floor(sortedHeights.length / 2)];
        const uneven = heights.filter((height) => Math.abs(height - medianHeight) > KPI_HEIGHT_TOLERANCE_PX);
        if (uneven.length) pushWarning(`kpiHeightMismatch=${uneven.length}`);
    }

    if (!isMobile && metrics.spacing.kpiHeights?.length >= 4) {
        const firstRow = metrics.spacing.kpiHeights.slice(0, 4);
        const variance = Math.max(...firstRow) - Math.min(...firstRow);
        if (variance > KPI_ROW_VARIANCE_MAX) pushWarning(`kpiRowHeightVariance=${variance}`);
    }

    if (KPI_CARD_REQUIRED_SCOPES.some((scope) => name.includes(scope)) && metrics.spacing.cardsMissingKpiClass?.length) {
        pushWarning(`cardsMissingKpiClass=${metrics.spacing.cardsMissingKpiClass.length}`);
    }
    if (metrics.spacing.cardBorderRadiusIssues?.length) {
        pushWarning(`cardBorderRadiusMismatch=${metrics.spacing.cardBorderRadiusIssues.length}`);
    }

    if (metrics.layoutShift.value > LAYOUT_SHIFT_THRESHOLD) pushHard(`layoutShift=${metrics.layoutShift.value.toFixed(4)}`);
    if (metrics.componentLayoutShift?.length) pushHard(`componentLayoutShift=${metrics.componentLayoutShift.length}`);
    if (metrics.contrastProblems.length) pushHard(`contrastProblems=${metrics.contrastProblems.length}`);
    if (metrics.visualContainmentIssues?.length) pushWarning(`visualContainmentIssues=${metrics.visualContainmentIssues.length}`);
    if (metrics.formSwitchMarginIssues?.length) pushWarning(`formSwitchMarginIssues=${metrics.formSwitchMarginIssues.length}`);
    if (metrics.formSwitchProportionIssues?.length) pushHard(`formSwitchProportionIssues=${metrics.formSwitchProportionIssues.length}`);
    if (metrics.formSwitchHeightIssues?.length) pushHard(`formSwitchHeightIssues=${metrics.formSwitchHeightIssues.length}`);
    if (metrics.inputGroupHeightIssues?.length) pushHard(`inputGroupHeightIssues=${metrics.inputGroupHeightIssues.length}`);

    const relevantConsoleEntries = report.network.consoleEntries.filter((entry) => !isKnownAuditConsoleNoise(entry, report.name));
    const relevantRequestFailures = report.network.requestFailures.filter((entry) => !isExpectedRequestFailure(entry));

    if (report.diff.ratio > VISUAL_DRIFT_THRESHOLD) pushHard(`visualDrift=${report.diff.ratio.toFixed(4)}`);
    if (relevantConsoleEntries.length) pushHard(`console=${relevantConsoleEntries.length}`);
    if (report.network.pageErrors.length) pushHard(`pageErrors=${report.network.pageErrors.length}`);
    if (relevantRequestFailures.length) pushHard(`failedRequests=${relevantRequestFailures.length}`);
    if (report.network.badResponses.length) pushHard(`badResponses=${report.network.badResponses.length}`);
    if (report.diff.sizeMismatch) pushHard('screenshotSizeMismatch');

    const duplicateRequestMap = new Map();
    for (const entry of report.network.requests || []) {
        if (!entry?.url) continue;
        const method = String(entry.method || 'GET').toUpperCase();
        const url = normalizeRequestUrl(entry.url);
        const key = `${method} ${url}`;
        const existing = duplicateRequestMap.get(key);
        if (existing) {
            existing.count += 1;
            continue;
        }
        duplicateRequestMap.set(key, { method, url, count: 1 });
    }
    report.network.duplicateRequests = Array.from(duplicateRequestMap.values())
        .filter((entry) => entry.count > DUPLICATE_REQUEST_THRESHOLD)
        .sort((a, b) => b.count - a.count)
        .slice(0, 10);
    if (report.network.duplicateRequests.length) pushWarning(`duplicateRequests=${report.network.duplicateRequests.length}`);

    if (isLoginError) {
        const loginFailure = metrics.loginFailure || {};
        const errorText = String(loginFailure.errorText || '').toLowerCase();
        const alertPresent = Boolean(loginFailure.alertVisible || errorText.length > 0);
        const isRateLimited = errorText.includes('too many') || errorText.includes('try again later');
        if (isRateLimited) {
            pushWarning('rateLimited');
        } else {
            if (!alertPresent) pushHard('loginErrorAlertMissing');
            if (loginFailure.submitButtonDisabled) pushHard('loginSubmitStillDisabled');
        }
    }

    return {
        findings: [...hardFindings, ...warnings],
        hardFindings,
        warnings,
    };
}

/**
 * Return true when a 404 response for /status is expected for the given view.
 */
export function isExpectedStatusUnavailable(view, response) {
    if (view.scope !== 'status' || !response) return false;
    try {
        return new URL(response.url()).pathname === '/status' && response.status() === 404;
    } catch {
        return false;
    }
}

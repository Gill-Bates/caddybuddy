//
// tools/ui-lint/lib/result-serializer.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

function countItems(value) {
    return Array.isArray(value) ? value.length : 0;
}

function collectDetails(value) {
    return Array.isArray(value) ? value : [];
}

const URL_IN_TEXT_RE = /https?:\/\/[^\s"'<>]+/g;
const SENSITIVE_TEXT_RE = /\b(token|secret|password|passwd|csrf|session|authorization)\b\s*[:=]\s*["']?[^"'\s]+/gi;
const MAX_SUMMARY_TEXT_LENGTH = 1000;

function redactSummaryText(value) {
    return String(value ?? '')
        .replace(URL_IN_TEXT_RE, '[redacted-url]')
        .replace(SENSITIVE_TEXT_RE, '$1=[redacted]')
        .slice(0, MAX_SUMMARY_TEXT_LENGTH);
}

function sanitizeDetailValue(value) {
    if (typeof value === 'string') {
        return redactSummaryText(value);
    }
    if (Array.isArray(value)) {
        return value.map((entry) => sanitizeDetailValue(entry));
    }
    if (value && typeof value === 'object') {
        return Object.fromEntries(
            Object.entries(value).map(([key, entryValue]) => [key, sanitizeDetailValue(entryValue)]),
        );
    }
    return value;
}

function sanitizeDetails(value) {
    return collectDetails(value).map((entry) => sanitizeDetailValue(entry));
}

export function serializeResultForOutput(result, { summaryPath, visualRegressionEnabled }) {
    const metrics = result.metrics ?? {};
    const spacing = metrics.spacing ?? {};
    const horizontalOverflow = metrics.horizontalOverflow ?? {};
    const network = result.network ?? {};
    const layoutShiftValue = Number(metrics.layoutShift?.value ?? 0);

    return {
        name: result.name,
        url: result.url,
        theme: result.theme || null,
        findings: result.findings,
        hardFindings: result.hardFindings ?? [],
        warnings: result.warnings ?? [],
        uiScore: metrics.uiScore ?? null,
        uiScoreLabel: metrics.uiScoreLabel ?? null,
        visualRegressionEnabled,
        visualRegressionPass: result.visualRegression?.pass ?? null,
        visualRegressionReason: result.visualRegression?.reason ?? null,
        visualRegressionDiffPercent: result.visualRegression?.percent ?? null,
        diffRatio: Number.isFinite(result.diff?.ratio) ? Number(result.diff.ratio.toFixed(6)) : null,
        layoutShift: Number(layoutShiftValue.toFixed(6)),
        overflowOffenders: countItems(horizontalOverflow.offenders),
        clippedButtons: countItems(metrics.clippedButtons),
        buttonAlignmentIssues: countItems(metrics.buttonAlignmentIssues),
        badgeAlignmentIssues: countItems(metrics.badgeAlignmentIssues),
        clickTargetsTooSmall: countItems(metrics.clickTargetsTooSmall),
        clickTargetDetails: sanitizeDetails(metrics.clickTargetsTooSmall),
        iconButtonsTouchBlocked: countItems(metrics.iconButtonsTouchBlocked),
        hiddenInteractive: countItems(metrics.hiddenInteractiveElements),
        bootstrapGridIssues: countItems(metrics.bootstrapGridIssues),
        bootstrapColumnsOutsideRows: countItems(metrics.bootstrapColumnsOutsideRows),
        breakpointDisplayConflicts: countItems(metrics.breakpointDisplayConflicts),
        navbarCollapseIssues: countItems(metrics.navbarCollapseIssues),
        focusOrderIssues: countItems(metrics.focusOrderIssues),
        focusIndicatorMissing: countItems(metrics.focusIndicatorMissing),
        scrollEdgeCrowding: countItems(metrics.scrollEdgeCrowding),
        scrollBottomCrowding: countItems(metrics.scrollBottomCrowding),
        footerViewportGap: metrics.footerViewportGap?.gapPx ?? null,
        footerViewportGapPass: metrics.footerViewportGap?.present ? (metrics.footerViewportGap.passesMinimum ? 1 : 0) : null,
        sidebarFooterViewportGap: metrics.sidebarFooterViewportGap?.gapPx ?? null,
        sidebarFooterViewportGapPass: metrics.sidebarFooterViewportGap?.present ? (metrics.sidebarFooterViewportGap.passesMinimum ? 1 : 0) : null,
        sitesTableRowCount: metrics.sitesTableDensity?.rowCount ?? null,
        sitesTableMedianRowHeight: metrics.sitesTableDensity?.medianRowHeightPx ?? null,
        sitesTableMaxRowHeight: metrics.sitesTableDensity?.maxRowHeightPx ?? null,
        sitesTableRowsTooTall: countItems(metrics.sitesTableDensity?.oversizedRows),
        ssllabsMobileCardLayout: metrics.ssllabsMobileCardLayout?.present ? 1 : 0,
        ssllabsMobileCardRowCount: metrics.ssllabsMobileCardLayout?.rowCount ?? null,
        ssllabsMobileCardTheadHidden: metrics.ssllabsMobileCardLayout?.present
            ? (metrics.ssllabsMobileCardLayout.theadHidden ? 1 : 0)
            : null,
        ssllabsMobileCardIssues: countItems(metrics.ssllabsMobileCardLayout?.issues),
        ssllabsFilterbarHeightPx: metrics.ssllabsFilterbarHeightIssue?.height ?? null,
        ssllabsFilterbarHeightPass: metrics.ssllabsFilterbarHeightIssue?.present
            ? (metrics.ssllabsFilterbarHeightIssue.passesMaximum ? 1 : 0)
            : null,
        ssllabsInlineSchedulerTooNarrow: countItems(metrics.ssllabsInlineSchedulerLayout?.tooNarrow),
        ssllabsInlineSchedulerTooWide: countItems(metrics.ssllabsInlineSchedulerLayout?.tooWide),
        ssllabsInlineSchedulerAlignmentVariance: metrics.ssllabsInlineSchedulerLayout?.alignmentVariance ?? null,
        ssllabsRetentionLayoutWidthDelta: metrics.ssllabsRetentionLayout?.widthDelta ?? null,
        ssllabsRetentionLayoutEdgeDelta: metrics.ssllabsRetentionLayout?.edgeDelta ?? null,
        ssllabsRetentionLayoutSpacingVariance: metrics.ssllabsRetentionLayout?.spacingVariance ?? null,
        ssllabsRetentionLayoutPass: metrics.ssllabsRetentionLayout?.present
            ? (metrics.ssllabsRetentionLayout.passesAlignment ? 1 : 0)
            : null,
        dashboardHeroMetricInsetVariance: metrics.dashboardHeroMetricInsets?.variance ?? null,
        dashboardHeroMetricInsetLeft: metrics.dashboardHeroMetricInsets?.leftInset ?? null,
        dashboardHeroMetricInsetRight: metrics.dashboardHeroMetricInsets?.rightInset ?? null,
        onboardingWizardStepActiveButtons: metrics.onboardingWizardStepDimming?.activeButtons ?? null,
        onboardingWizardStepInactiveButtons: metrics.onboardingWizardStepDimming?.inactiveButtons ?? null,
        onboardingWizardStepActiveOpacity: metrics.onboardingWizardStepDimming?.activeOpacity ?? null,
        onboardingWizardStepInactiveOpacityMin: metrics.onboardingWizardStepDimming?.inactiveOpacityMin ?? null,
        onboardingWizardStepInactiveOpacityMax: metrics.onboardingWizardStepDimming?.inactiveOpacityMax ?? null,
        onboardingWizardStepDimmingPass: metrics.onboardingWizardStepDimming?.present
            ? (metrics.onboardingWizardStepDimming.passesDimming ? 1 : 0)
            : null,
        onboardingWizardStepAccentPass: metrics.onboardingWizardStepAccent?.present
            ? (metrics.onboardingWizardStepAccent.passesAccent ? 1 : 0)
            : null,
        primaryPanelPaddingMismatch: countItems(metrics.primaryPanelPadding?.mismatches),
        pageStructureMissingRowWrapper: countItems(metrics.pageStructureConsistent?.issues),
        ghostScrollContainers: countItems(metrics.ghostScrollContainers),
        nestedScrollContainers: countItems(metrics.nestedScrollContainers),
        flexScrollTraps: countItems(metrics.flexScrollTraps),
        badgeStyleMismatches: countItems(metrics.badgeStyleMismatches),
        buttonContrastIssues: countItems(metrics.buttonContrastIssues),
        nonTokenColorUsage: countItems(metrics.nonTokenColorUsage),
        monospaceToneMismatches: countItems(metrics.monospaceToneMismatches),
        modalThemeIssues: countItems(metrics.modalThemeIssues),
        modalBackdropBlur: metrics.modalBackdrop?.blurPx ?? null,
        modalBackdropSaturate: metrics.modalBackdrop?.saturate ?? null,
        modalBackdropAlpha: metrics.modalBackdrop?.alpha ?? null,
        contrastProblems: countItems(metrics.contrastProblems),
        componentLayoutShift: countItems(metrics.componentLayoutShift),
        visualContainmentIssues: countItems(metrics.visualContainmentIssues),
        mobileRowCardStackGapRows: countItems(spacing.mobileRowCardStackGaps),
        mobileRowCardStackGapIssues: countItems(spacing.mobileRowCardStackGaps?.filter((entry) => !entry.gapsConsistent)),
        mobileCardEdgeAlignment: spacing.mobileCardEdgeAlignment ? 1 : 0,
        mobileCardEdgeAlignmentIssues: spacing.mobileCardEdgeAlignment
            ? [!spacing.mobileCardEdgeAlignment.matchesLeft, !spacing.mobileCardEdgeAlignment.matchesRight].filter(Boolean).length
            : 0,
        mobileTopbarClearance: metrics.mobileTopbarClearance?.present ? 1 : 0,
        mobileTopbarClearancePx: metrics.mobileTopbarClearance?.clearancePx ?? null,
        mobileTopbarClearancePass: metrics.mobileTopbarClearance?.present
            ? (metrics.mobileTopbarClearance.passesClearance ? 1 : 0)
            : null,
        desktopPrimaryPanelHeightAlignmentDelta: metrics.desktopPrimaryPanelHeightAlignment?.delta ?? null,
        desktopPrimaryPanelHeightAlignmentPass: metrics.desktopPrimaryPanelHeightAlignment?.present
            ? (metrics.desktopPrimaryPanelHeightAlignment.passesTolerance ? 1 : 0)
            : null,
        duplicateRequests: countItems(network.duplicateRequests),
        badResponseDetails: sanitizeDetails(network.badResponses),
        failedRequestDetails: sanitizeDetails(network.requestFailures),
        kpiCards: countItems(spacing.kpiCards),
        kpiMissingClass: countItems(spacing.cardsMissingKpiClass),
        cardBorderRadiusMismatch: countItems(spacing.cardBorderRadiusIssues),
        kpiHeightVariance: spacing.kpiHeightVariance || 0,
        dashboardHeroMetricTooTall: countItems(metrics.dashboardHeroMetricHeights?.tooTall),
        loginErrorVisible: Boolean(metrics.loginFailure?.alertVisible),
        loginShakeActive: Boolean(metrics.loginFailure?.cardAnimationActive),
        loginPasswordInvalid: Boolean(metrics.loginFailure?.passwordInvalidClass),
        loginRateLimitAttempts: metrics.loginRateLimit?.attempts ?? null,
        loginRateLimit429: metrics.loginRateLimit?.reached429 ?? null,
        loginRateLimitStatus: metrics.loginRateLimit?.status ?? null,
        missingSecurityHeaders: countItems(result.securityHeaders?.missing),
        errorMessage: result.error?.message ? redactSummaryText(result.error.message) : null,
        errorPhase: result.error?.phase ?? null,
        errorDevice: result.error?.device ?? null,
        summaryPath,
    };
}

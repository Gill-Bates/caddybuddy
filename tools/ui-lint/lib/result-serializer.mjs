//
// tools/ui-lint/lib/result-serializer.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

function countItems(value) {
    return Array.isArray(value) ? value.length : 0;
}

export function serializeResultForOutput(result, { summaryPath, visualRegressionEnabled }) {
    const metrics = result.metrics ?? {};
    const spacing = metrics.spacing ?? {};
    const horizontalOverflow = metrics.horizontalOverflow ?? {};
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
        clickTargetsTooSmall: countItems(metrics.clickTargetsTooSmall),
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
        primaryPanelPaddingMismatch: countItems(metrics.primaryPanelPadding?.mismatches),
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
        duplicateRequests: countItems(result.network.duplicateRequests),
        kpiCards: countItems(spacing.kpiCards),
        kpiMissingClass: countItems(spacing.cardsMissingKpiClass),
        cardBorderRadiusMismatch: countItems(spacing.cardBorderRadiusIssues),
        kpiHeightVariance: spacing.kpiHeightVariance || 0,
        loginErrorVisible: Boolean(metrics.loginFailure?.alertVisible),
        loginShakeActive: Boolean(metrics.loginFailure?.cardAnimationActive),
        loginPasswordInvalid: Boolean(metrics.loginFailure?.passwordInvalidClass),
        loginRateLimitAttempts: metrics.loginRateLimit?.attempts ?? null,
        loginRateLimit429: metrics.loginRateLimit?.reached429 ?? null,
        loginRateLimitStatus: metrics.loginRateLimit?.status ?? null,
        missingSecurityHeaders: countItems(result.securityHeaders?.missing),
        errorMessage: result.error?.message ?? null,
        errorPhase: result.error?.phase ?? null,
        errorDevice: result.error?.device ?? null,
        summaryPath,
    };
}
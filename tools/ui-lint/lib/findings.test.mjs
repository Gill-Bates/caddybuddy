//
// tools/ui-lint/lib/findings.test.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import test from 'node:test';
import assert from 'node:assert/strict';

import { summarizeFindings } from './findings.mjs';
import { serializeResultForOutput } from './result-serializer.mjs';

test('summarizeFindings keeps click target failures hard and single tall table rows as a warning', () => {
    const result = summarizeFindings({
        name: 'desktop-sites',
        metrics: {
            clickTargetsTooSmall: [{ tag: 'BUTTON', width: 32, height: 32 }],
            managementTableDensity: {
                present: true,
                maximumRowHeight: 64,
                targetRowHeight: 48,
                tolerance: 2,
                rowCount: 6,
                medianRowHeightPx: 47,
                maxRowHeightPx: 72,
                passesTarget: true,
                oversizedRows: [
                    { index: 3, text: 'Example row', height: 72 },
                ],
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('clickTargetsTooSmall=1'));
    assert.ok(result.warnings.includes('tableRowsTooTall=1/72/64'));
    assert.ok(!result.findings.some((finding) => finding.startsWith('tableRowMedianTooTall=')));
    assert.ok(result.findings.includes('tableRowsTooTall=1/72/64'));
});

test('summarizeFindings fails a management table whose ordinary rows miss the density target', () => {
    const result = summarizeFindings({
        name: 'desktop-ssllabs-light',
        metrics: {
            managementTableDensity: {
                present: true,
                maximumRowHeight: 72,
                targetRowHeight: 48,
                tolerance: 2,
                rowCount: 4,
                medianRowHeightPx: 64,
                maxRowHeightPx: 66,
                passesTarget: false,
                oversizedRows: [],
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('tableRowMedianTooTall=64/48'));
});

test('summarizeFindings reports shared table rhythm drift as hard findings and serializes details', () => {
    const toolbar = { tag: 'DIV', className: 'ssllabs-filterbar__quick-filters', text: 'All 4', height: 36, expected: 34 };
    const tableRhythm = {
        present: true,
        touchSized: false,
        controlSizeMismatches: [{ tag: 'BUTTON', className: 'btn btn-sm btn--icon-only', text: 'Edit', height: 44, expected: 32 }],
        toolbarHeightMismatches: [toolbar],
        actionGapMismatches: [{ tag: 'DIV', className: 'cell-actions', text: '', gap: 6.4, expected: 6 }],
        cellPaddingMismatches: [{ tag: 'TD', className: '', text: 'fastapi', paddingTop: 12.8, paddingBottom: 12.8, expected: 6 }],
    };
    const result = summarizeFindings({
        name: 'desktop-ssllabs-light',
        metrics: { tableRhythm },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    for (const finding of ['tableControlSizes=1', 'tableToolbarHeights=1', 'tableActionGaps=1', 'tableCellPadding=1']) {
        assert.ok(result.hardFindings.includes(finding), finding);
    }

    const output = serializeResultForOutput({
        ...result,
        metrics: { horizontalOverflow: { offenders: [] }, spacing: {}, layoutShift: { value: 0 }, tableRhythm },
    }, { summaryPath: '/tmp/ui-lint-summary.json', visualRegressionEnabled: false });

    assert.equal(output.tableToolbarHeights, 1);
    assert.deepEqual(output.tableToolbarHeightDetails, [toolbar]);
    assert.equal(output.tableCellPadding, 1);
});

test('summarizeFindings reports maintenance editor toolbar drift as hard findings and serializes details', () => {
    const tool = { tag: 'BUTTON', className: 'btn btn-sm maintenance-editor__tool', text: 'Heading 1', width: 42, height: 36, expected: 36 };
    const group = { tag: 'DIV', className: 'maintenance-editor__group', text: 'Insert link Clear formatting', lineSpreadPx: 44 };
    const editorToolbar = { present: true, touchSized: false, toolSizeMismatches: [tool], splitGroups: [group] };
    const result = summarizeFindings({
        name: 'laptop-settings-light',
        metrics: { editorToolbar },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('editorToolSizes=1'));
    assert.ok(result.hardFindings.includes('editorToolGroupsSplit=1'));

    const output = serializeResultForOutput({
        ...result,
        metrics: { horizontalOverflow: { offenders: [] }, spacing: {}, layoutShift: { value: 0 }, editorToolbar },
    }, { summaryPath: '/tmp/ui-lint-summary.json', visualRegressionEnabled: false });

    assert.equal(output.editorToolSizes, 1);
    assert.deepEqual(output.editorToolSizeDetails, [tool]);
    assert.equal(output.editorToolGroupsSplit, 1);
    assert.deepEqual(output.editorToolGroupsSplitDetails, [group]);
});

test('summarizeFindings treats About page value font-size drift as a hard finding', () => {
    const result = summarizeFindings({
        name: 'desktop-about-light',
        metrics: {
            aboutValueFontSizeMismatches: [
                { className: 'about-meta-value', text: '1.5.1', fontSize: 13.12, expected: 14 },
            ],
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('aboutValueFontSizeMismatches=1'));
    assert.ok(result.findings.includes('aboutValueFontSizeMismatches=1'));
});

test('summarizeFindings reports iOS input zoom risks as hard findings and serializes details', () => {
    const risk = { tag: 'INPUT', id: 'sites-search', className: 'sites-search__input', fontSize: 13.12, minimum: 16 };
    const result = summarizeFindings({
        name: 'mobile-sites-light',
        metrics: { inputZoomRisks: [risk] },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('inputZoomRisks=1'));
    assert.ok(result.findings.includes('inputZoomRisks=1'));

    const output = serializeResultForOutput({
        ...result,
        metrics: { horizontalOverflow: { offenders: [] }, spacing: {}, layoutShift: { value: 0 }, inputZoomRisks: [risk] },
    }, { summaryPath: '/tmp/ui-lint-summary.json', visualRegressionEnabled: false });

    assert.equal(output.inputZoomRisks, 1);
    assert.deepEqual(output.inputZoomRiskDetails, [risk]);
});

test('summarizeFindings flags a body background that hides the root page gradient', () => {
    const summarize = (pageBackdrop) => summarizeFindings({
        name: 'desktop-dashboard-light',
        metrics: { pageBackdrop },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    const hidden = summarize({ present: true, htmlHasGradient: true, bodyBackgroundColor: 'rgb(255, 255, 255)', bodyHasImage: false, passesBackdrop: false });
    assert.ok(hidden.hardFindings.includes('pageBackdropHidden=1/rgb(255, 255, 255)'));

    const visible = summarize({ present: true, htmlHasGradient: true, bodyBackgroundColor: 'rgba(0, 0, 0, 0)', bodyHasImage: false, passesBackdrop: true });
    assert.ok(!visible.findings.some((finding) => finding.startsWith('pageBackdropHidden')));
});

test('summarizeFindings reports toasts left on screen at rest and serializes details', () => {
    const offender = { index: 0, stackSize: 3, visibleWidth: 350, visibleHeight: 49, left: 28, top: 645, viewportWidth: 390, viewportHeight: 844 };
    const toastExit = { present: true, usesExitOffsetHook: true, offenders: [offender] };
    const result = summarizeFindings({
        name: 'mobile-dashboard-light',
        metrics: { toastExit },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('toastExitVisible=1'));

    const output = serializeResultForOutput({
        ...result,
        metrics: { horizontalOverflow: { offenders: [] }, spacing: {}, layoutShift: { value: 0 }, toastExit },
    }, { summaryPath: '/tmp/ui-lint-summary.json', visualRegressionEnabled: false });

    assert.equal(output.toastExitVisible, 1);
    assert.deepEqual(output.toastExitDetails, [offender]);
});

test('summarizeFindings reports backdrop filters kept under reduced transparency', () => {
    const offender = { tag: 'DIV', id: 'sidebarBackdrop', className: 'sidebar-backdrop', backdropFilter: 'blur(4px)' };
    const summarize = (reducedTransparencyBackdrop) => summarizeFindings({
        name: 'mobile-dashboard-light',
        metrics: { reducedTransparencyBackdrop },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(summarize({ present: true, offenders: [offender] }).hardFindings.includes('reducedTransparencyBackdrop=1'));
    for (const quiet of [{ present: true, offenders: [] }, null]) {
        assert.ok(!summarize(quiet).findings.some((finding) => finding.startsWith('reducedTransparencyBackdrop')));
    }

    const skipped = serializeResultForOutput({
        ...summarize(null),
        metrics: { horizontalOverflow: { offenders: [] }, spacing: {}, layoutShift: { value: 0 }, reducedTransparencyBackdrop: null },
    }, { summaryPath: '/tmp/ui-lint-summary.json', visualRegressionEnabled: false });
    // null = not measured (non-Chromium engine), distinct from 0 = measured and clean.
    assert.equal(skipped.reducedTransparencyBackdrops, null);
});

test('summarizeFindings stays quiet without input zoom risks', () => {
    const result = summarizeFindings({
        name: 'desktop-sites-light',
        metrics: { inputZoomRisks: [] },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(!result.findings.some((finding) => finding.startsWith('inputZoomRisks')));
});

test('summarizeFindings treats scheduler drift and oversized dashboard heros as warnings', () => {
    const result = summarizeFindings({
        name: 'desktop-dashboard',
        metrics: {
            ssllabsInlineSchedulerLayout: {
                present: true,
                alignmentVariance: 6,
                alignmentTolerance: 2,
                tooNarrow: [],
                tooWide: [{ width: 212 }],
                passesAlignment: false,
            },
            dashboardHeroMetricInsets: {
                present: true,
                leftInset: 16,
                rightInset: 22,
                variance: 6,
                maximum: 2,
                passesVariance: false,
            },
            dashboardHeroMetricHeights: {
                present: true,
                tooTall: [{ height: 156 }],
            },
            spacing: {},
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.warnings.includes('ssllabsInlineSchedulerTooWide=1'));
    assert.ok(result.warnings.includes('ssllabsInlineSchedulerAlignment=6/2'));
    assert.ok(result.warnings.includes('dashboardHeroMetricInsetVariance=6/2'));
    assert.ok(result.warnings.includes('dashboardHeroMetricTooTall=1/145'));
});

test('summarizeFindings warns when the retention scale and tick labels drift apart', () => {
    const result = summarizeFindings({
        name: 'desktop-settings',
        metrics: {
            ssllabsRetentionLayout: {
                present: true,
                widthDelta: 4,
                edgeDelta: 3,
                leftDelta: 2,
                rightDelta: 3,
                spacingVariance: 3,
                widthTolerance: 2,
                edgeTolerance: 2,
                spacingTolerance: 2,
                passesAlignment: false,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.warnings.includes('ssllabsRetentionLayout=4/2/3/3/2/2/2'));
});

test('summarizeFindings treats misaligned desktop settings columns as a hard finding', () => {
    const result = summarizeFindings({
        name: 'desktop-settings',
        metrics: {
            desktopPrimaryPanelHeightAlignment: {
                present: true,
                heights: [612, 564],
                delta: 48,
                tolerance: 3,
                passesTolerance: false,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('desktopPrimaryPanelHeightAlignment=48/3'));
    assert.ok(result.findings.includes('desktopPrimaryPanelHeightAlignment=48/3'));
});

test('summarizeFindings treats page header content gaps that drift from the Dashboard as hard findings', () => {
    const result = summarizeFindings({
        name: 'desktop-sites',
        metrics: {
            pageHeaderContentGap: {
                present: true,
                gapPx: 20,
                expected: 35.2,
                tolerance: 2,
                delta: 15.2,
                passesTolerance: false,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('pageHeaderContentGap=20/35.2/2'));
});

test('summarizeFindings treats page header content offsets as hard findings', () => {
    const result = summarizeFindings({
        name: 'desktop-ssllabs',
        metrics: {
            pageHeaderContentAlignment: {
                present: true,
                offsetPx: 12,
                tolerance: 2,
                passesTolerance: false,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('pageHeaderContentAlignment=12/2'));
});

test('summarizeFindings warns when the SSL Labs history loading shell contract is missing', () => {
    const result = summarizeFindings({
        name: 'desktop-dashboard',
        metrics: {
            ssllabsHistoryLoadingShell: {
                present: true,
                hasShellMarker: false,
                hasEmptyState: true,
                hasToolbar: true,
                hasInspector: false,
                hasCanvas: true,
                hasPeriodList: false,
                passesShell: false,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.warnings.includes('ssllabsHistoryLoadingShell=0/1/1/0/1/0'));
});

test('summarizeFindings treats an oversized SSL Labs desktop filterbar as a hard finding', () => {
    const result = summarizeFindings({
        name: 'desktop-ssllabs-light',
        metrics: {
            ssllabsFilterbarHeightIssue: {
                present: true,
                height: 58,
                maximum: 52,
                passesMaximum: false,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('ssllabsFilterbarHeight=58/52'));
});

test('summarizeFindings ignores the SSL Labs filterbar height metric on mobile', () => {
    const result = summarizeFindings({
        name: 'mobile-ssllabs-light',
        metrics: {
            ssllabsFilterbarHeightIssue: {
                present: true,
                height: 142,
                maximum: 52,
                passesMaximum: false,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(!result.findings.some((entry) => entry.startsWith('ssllabsFilterbarHeight=')));
});

test('summarizeFindings flags SSL Labs mobile site rows that fail the card contract', () => {
    const result = summarizeFindings({
        name: 'mobile-ssllabs-light',
        metrics: {
            ssllabsMobileCardLayout: {
                present: true,
                rowCount: 3,
                minBorderRadius: 8,
                theadHidden: true,
                issues: [
                    { index: 0, host: 'example.com', reasons: ['noCardRadius', 'noCardBorder'] },
                ],
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('ssllabsMobileCardLayout=1/1/3'));
    assert.ok(result.findings.includes('ssllabsMobileCardLayout=1/1/3'));
});

test('summarizeFindings flags SSL Labs mobile layout when the table head stays visible', () => {
    const result = summarizeFindings({
        name: 'mobile-ssllabs-light',
        metrics: {
            ssllabsMobileCardLayout: {
                present: true,
                rowCount: 2,
                minBorderRadius: 8,
                theadHidden: false,
                issues: [],
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('ssllabsMobileCardLayout=0/0/2'));
});

test('summarizeFindings stays silent when SSL Labs mobile cards satisfy the contract', () => {
    const result = summarizeFindings({
        name: 'mobile-ssllabs-light',
        metrics: {
            ssllabsMobileCardLayout: {
                present: true,
                rowCount: 4,
                minBorderRadius: 8,
                theadHidden: true,
                issues: [],
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(!result.findings.some((entry) => entry.startsWith('ssllabsMobileCardLayout=')));
});

test('summarizeFindings treats broken desktop sites form fill as a hard finding', () => {
    const result = summarizeFindings({
        name: 'desktop-sites',
        metrics: {
            sitesFormLayout: {
                present: true,
                maximumEditorBottomGap: 16,
                maximumActionsGap: 20,
                editorBottomGapPx: 88,
                actionsGapPx: 12,
                passesEditorBottomGap: false,
                passesActionsGap: true,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('sitesFormLayout=88/16/12/20'));
    assert.ok(result.findings.includes('sitesFormLayout=88/16/12/20'));
});

test('summarizeFindings warns when onboarding wizard tiles are not dimmed', () => {
    const result = summarizeFindings({
        name: 'desktop-onboarding-light',
        metrics: {
            onboardingWizardStepDimming: {
                present: true,
                activeButtons: 1,
                inactiveButtons: 2,
                activeOpacity: 1,
                inactiveOpacityMin: 0.95,
                inactiveOpacityMax: 0.95,
                expectedActiveMinimum: 0.95,
                expectedInactiveMaximum: 0.8,
                passesDimming: false,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.warnings.includes('onboardingWizardStepDimming=1/0.95/0.95/0.8'));
});

test('summarizeFindings warns when multiple onboarding wizard tiles are active', () => {
    const result = summarizeFindings({
        name: 'desktop-onboarding-light',
        metrics: {
            onboardingWizardStepDimming: {
                present: true,
                activeButtons: 3,
                inactiveButtons: 0,
                activeOpacity: null,
                inactiveOpacityMin: null,
                inactiveOpacityMax: null,
                expectedActiveMinimum: 0.95,
                expectedInactiveMaximum: 0.8,
                passesDimming: false,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.warnings.includes('onboardingWizardStepActiveState=3/0'));
});

test('summarizeFindings warns when the active onboarding tile loses its accent strip', () => {
    const result = summarizeFindings({
        name: 'desktop-onboarding-light',
        metrics: {
            onboardingWizardStepAccent: {
                present: true,
                activeButtons: 1,
                inactiveButtons: 2,
                activeBoxShadow: '0px 14px 28px rgba(15, 118, 110, 0.12)',
                passesAccent: false,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.warnings.includes('onboardingWizardStepAccent=1/2'));
});

test('summarizeFindings warns when inactive onboarding step points keep the active palette', () => {
    const result = summarizeFindings({
        name: 'desktop-onboarding-light',
        metrics: {
            onboardingWizardStepIndexPalette: {
                present: true,
                activeButtons: 1,
                inactiveButtons: 2,
                activeBackground: 'rgba(15, 118, 110, 0.12)',
                inactiveBackground: 'rgba(15, 118, 110, 0.12)',
                activeColor: 'rgb(15, 118, 110)',
                inactiveColor: 'rgb(15, 118, 110)',
                passesPalette: false,
            },
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.warnings.includes('onboardingWizardStepIndexPalette=1/2'));
});

const CSP_STYLE_VIOLATION_TEXT = "Refused to apply a stylesheet because its hash, its nonce, or 'unsafe-inline' does not appear in the style-src directive of the Content Security Policy.";

test('summarizeFindings keeps CSP inline-style violations as hard console findings', () => {
    const result = summarizeFindings({
        name: 'chromium-desktop-sites-light',
        metrics: {},
        diff: { ratio: 0, sizeMismatch: false },
        network: {
            consoleEntries: [{ type: 'error', text: CSP_STYLE_VIOLATION_TEXT }],
        },
    });

    assert.ok(result.hardFindings.includes('console=1'));
});

// Playwright's WebKit screenshot path injects an unnonced stylesheet into every
// captured page, so on WebKit this violation is produced by the harness itself.
// Chromium and Firefox views keep asserting the contract (test above).
test('summarizeFindings ignores the WebKit screenshot stylesheet CSP violation', () => {
    const result = summarizeFindings({
        name: 'webkit-desktop-sites-light',
        metrics: {},
        diff: { ratio: 0, sizeMismatch: false },
        network: {
            consoleEntries: [{ type: 'error', text: CSP_STYLE_VIOLATION_TEXT }],
        },
    });

    assert.ok(!result.findings.some((finding) => finding.startsWith('console=')));
});

test('summarizeFindings treats missing focus indicators as hard failures', () => {
    const result = summarizeFindings({
        name: 'desktop-sites-light',
        metrics: {
            focusIndicatorMissing: [{ tag: 'BUTTON' }],
        },
        diff: { ratio: 0, sizeMismatch: false },
        network: {},
    });

    assert.ok(result.hardFindings.includes('focusIndicatorMissing=1'));
});

test('summarizeFindings keeps query-sensitive duplicate requests distinct', () => {
    const result = summarizeFindings({
        name: 'desktop-dashboard-light',
        metrics: {},
        diff: { ratio: 0, sizeMismatch: false },
        network: {
            requests: [
                { method: 'GET', url: 'http://localhost:8000/api/v1/history?range=30' },
                { method: 'GET', url: 'http://localhost:8000/api/v1/history?range=30' },
                { method: 'GET', url: 'http://localhost:8000/api/v1/history?range=30' },
                { method: 'GET', url: 'http://localhost:8000/api/v1/history?range=30' },
                { method: 'GET', url: 'http://localhost:8000/api/v1/history?range=730' },
                { method: 'GET', url: 'http://localhost:8000/api/v1/history?range=730' },
                { method: 'GET', url: 'http://localhost:8000/api/v1/history?range=730' },
                { method: 'GET', url: 'http://localhost:8000/api/v1/history?range=730' },
            ],
        },
    });

    assert.equal(result.warnings.filter((entry) => entry === 'duplicateRequests=2').length, 1);
});

test('serializeResultForOutput exposes sites density summary fields', () => {
    const output = serializeResultForOutput({
        name: 'desktop-sites',
        url: '/sites',
        findings: ['clickTargetsTooSmall=1'],
        hardFindings: ['clickTargetsTooSmall=1'],
        warnings: ['tableRowsTooTall=1/72/64'],
        diff: { ratio: 0, sizeMismatch: false },
        metrics: {
            horizontalOverflow: { offenders: [] },
            spacing: {},
            layoutShift: { value: 0 },
            dashboardHeroMetricInsets: {
                variance: 6,
                leftInset: 16,
                rightInset: 22,
            },
            managementTableDensity: {
                rowCount: 6,
                medianRowHeightPx: 58,
                maxRowHeightPx: 72,
                oversizedRows: [{}, {}],
            },
        },
        network: {},
    }, {
        summaryPath: '/tmp/ui-lint-summary.json',
        visualRegressionEnabled: false,
    });

    assert.equal(output.tableRowCount, 6);
    assert.equal(output.tableMedianRowHeight, 58);
    assert.equal(output.tableMaxRowHeight, 72);
    assert.equal(output.tableRowsTooTall, 2);
    assert.equal(output.dashboardHeroMetricInsetVariance, 6);
    assert.equal(output.dashboardHeroMetricInsetLeft, 16);
    assert.equal(output.dashboardHeroMetricInsetRight, 22);
});

test('serializeResultForOutput sanitizes detail payloads while preserving safe fields', () => {
    const output = serializeResultForOutput({
        name: 'desktop-login',
        url: '/login',
        findings: ['clickTargetsTooSmall=1', 'badResponses=1', 'failedRequests=1'],
        hardFindings: ['clickTargetsTooSmall=1', 'badResponses=1', 'failedRequests=1'],
        warnings: [],
        diff: { ratio: 0, sizeMismatch: false },
        metrics: {
            horizontalOverflow: { offenders: [] },
            spacing: {},
            layoutShift: { value: 0 },
            clickTargetsTooSmall: [
                { tag: 'A', className: 'cb-footer-link', text: 'GitHub', width: 28, height: 28, minimum: 32 },
            ],
        },
        network: {
            badResponses: [{ url: 'http://localhost:8000/login', status: 403 }],
            requestFailures: [{ url: 'http://localhost:8000/static/app.js', error: 'NS_BINDING_ABORTED' }],
        },
    }, {
        summaryPath: '/tmp/ui-lint-summary.json',
        visualRegressionEnabled: false,
    });

    assert.deepEqual(output.clickTargetDetails, [
        { tag: 'A', className: 'cb-footer-link', text: 'GitHub', width: 28, height: 28, minimum: 32 },
    ]);
    assert.deepEqual(output.badResponseDetails, [{ url: '[redacted-url]', status: 403 }]);
    assert.deepEqual(output.failedRequestDetails, [{ url: '[redacted-url]', error: 'NS_BINDING_ABORTED' }]);
});

test('serializeResultForOutput redacts sensitive URLs and secrets from detail payloads', () => {
    const output = serializeResultForOutput({
        name: 'desktop-login',
        url: '/login',
        findings: ['failedRequests=1'],
        hardFindings: ['failedRequests=1'],
        warnings: [],
        diff: { ratio: 0, sizeMismatch: false },
        error: {
            message: 'Login did not complete successfully: secret=abc123 (Final URL: http://localhost:8000/login?token=abc123, Content: See https://example.test/callback?password=secret)',
        },
        metrics: {
            horizontalOverflow: { offenders: [] },
            spacing: {},
            layoutShift: { value: 0 },
            clickTargetsTooSmall: [
                {
                    tag: 'A',
                    text: 'See https://example.test/callback?token=abc123',
                    href: 'https://example.test/callback?secret=xyz',
                },
            ],
        },
        network: {
            badResponses: [{ url: 'http://localhost:8000/login?token=abc123', status: 403 }],
            requestFailures: [{ url: 'http://localhost:8000/static/app.js?secret=xyz', error: 'token=abc123' }],
        },
    }, {
        summaryPath: '/tmp/ui-lint-summary.json',
        visualRegressionEnabled: false,
    });

    assert.deepEqual(output.clickTargetDetails, [
        {
            tag: 'A',
            text: 'See [redacted-url]',
            href: '[redacted-url]',
        },
    ]);
    assert.deepEqual(output.badResponseDetails, [{ url: '[redacted-url]', status: 403 }]);
    assert.deepEqual(output.failedRequestDetails, [{ url: '[redacted-url]', error: 'token=[redacted]' }]);
    assert.match(output.errorMessage, /secret=\[redacted\]/);
    assert.doesNotMatch(output.errorMessage, /token=abc123|password=secret|https:\/\/example\.test/);
});

test('serializeResultForOutput exposes scheduler and hero metric summary fields', () => {
    const output = serializeResultForOutput({
        name: 'desktop-dashboard',
        url: '/',
        findings: ['dashboardHeroMetricTooTall=1/145'],
        hardFindings: [],
        warnings: ['ssllabsInlineSchedulerTooWide=1'],
        diff: { ratio: 0, sizeMismatch: false },
        metrics: {
            horizontalOverflow: { offenders: [] },
            spacing: {},
            layoutShift: { value: 0 },
            ssllabsInlineSchedulerLayout: {
                tooNarrow: [],
                tooWide: [{ width: 212 }],
                alignmentVariance: 6,
            },
            ssllabsFilterbarHeightIssue: {
                present: true,
                height: 48,
                passesMaximum: true,
            },
            ssllabsRetentionLayout: {
                present: true,
                widthDelta: 1,
                edgeDelta: 2,
                spacingVariance: 1,
                passesAlignment: true,
            },
            dashboardHeroMetricHeights: {
                tooTall: [{ height: 156 }],
            },
        },
        network: {},
    }, {
        summaryPath: '/tmp/ui-lint-summary.json',
        visualRegressionEnabled: false,
    });

    assert.equal(output.ssllabsInlineSchedulerTooWide, 1);
    assert.equal(output.ssllabsInlineSchedulerAlignmentVariance, 6);
    assert.equal(output.ssllabsFilterbarHeightPx, 48);
    assert.equal(output.ssllabsFilterbarHeightPass, 1);
    assert.equal(output.ssllabsRetentionLayoutWidthDelta, 1);
    assert.equal(output.ssllabsRetentionLayoutEdgeDelta, 2);
    assert.equal(output.ssllabsRetentionLayoutSpacingVariance, 1);
    assert.equal(output.ssllabsRetentionLayoutPass, 1);
    assert.equal(output.dashboardHeroMetricTooTall, 1);
});

test('serializeResultForOutput exposes onboarding wizard dimming summary fields', () => {
    const output = serializeResultForOutput({
        name: 'desktop-onboarding',
        url: '/onboarding',
        findings: [],
        hardFindings: [],
        warnings: [],
        diff: { ratio: 0, sizeMismatch: false },
        metrics: {
            horizontalOverflow: { offenders: [] },
            spacing: {},
            layoutShift: { value: 0 },
            onboardingWizardStepDimming: {
                present: true,
                activeButtons: 1,
                inactiveButtons: 2,
                activeOpacity: 1,
                inactiveOpacityMin: 0.68,
                inactiveOpacityMax: 0.68,
                passesDimming: true,
            },
            onboardingWizardStepAccent: {
                present: true,
                passesAccent: true,
            },
        },
        network: {},
    }, {
        summaryPath: '/tmp/ui-lint-summary.json',
        visualRegressionEnabled: false,
    });

    assert.equal(output.onboardingWizardStepActiveButtons, 1);
    assert.equal(output.onboardingWizardStepInactiveButtons, 2);
    assert.equal(output.onboardingWizardStepActiveOpacity, 1);
    assert.equal(output.onboardingWizardStepInactiveOpacityMin, 0.68);
    assert.equal(output.onboardingWizardStepInactiveOpacityMax, 0.68);
    assert.equal(output.onboardingWizardStepDimmingPass, 1);
    assert.equal(output.onboardingWizardStepAccentPass, 1);
});

test('serializeResultForOutput exposes desktop settings column alignment summary fields', () => {
    const output = serializeResultForOutput({
        name: 'desktop-settings',
        url: '/settings',
        findings: [],
        hardFindings: [],
        warnings: [],
        diff: { ratio: 0, sizeMismatch: false },
        metrics: {
            horizontalOverflow: { offenders: [] },
            spacing: {},
            layoutShift: { value: 0 },
            desktopPrimaryPanelHeightAlignment: {
                present: true,
                heights: [612, 564],
                delta: 48,
                tolerance: 3,
                passesTolerance: false,
            },
        },
        network: {},
    }, {
        summaryPath: '/tmp/ui-lint-summary.json',
        visualRegressionEnabled: false,
    });

    assert.equal(output.desktopPrimaryPanelHeightAlignmentDelta, 48);
    assert.equal(output.desktopPrimaryPanelHeightAlignmentPass, 0);
});

test('serializeResultForOutput exposes the page header content gap contract', () => {
    const output = serializeResultForOutput({
        name: 'desktop-sites',
        url: '/sites',
        findings: [],
        hardFindings: [],
        warnings: [],
        diff: { ratio: 0, sizeMismatch: false },
        metrics: {
            horizontalOverflow: { offenders: [] },
            spacing: {},
            layoutShift: { value: 0 },
            pageHeaderContentGap: {
                present: true,
                gapPx: 35.2,
                expected: 35.2,
                delta: 0,
                passesTolerance: true,
            },
            pageHeaderContentAlignment: {
                present: true,
                offsetPx: 0,
                passesTolerance: true,
            },
        },
        network: {},
    }, {
        summaryPath: '/tmp/ui-lint-summary.json',
        visualRegressionEnabled: false,
    });

    assert.equal(output.pageHeaderContentGapPx, 35.2);
    assert.equal(output.pageHeaderContentGapExpectedPx, 35.2);
    assert.equal(output.pageHeaderContentGapDeltaPx, 0);
    assert.equal(output.pageHeaderContentGapPass, 1);
    assert.equal(output.pageHeaderContentAlignmentOffsetPx, 0);
    assert.equal(output.pageHeaderContentAlignmentPass, 1);
});

test('serializeResultForOutput tolerates missing network payloads', () => {
    const output = serializeResultForOutput({
        name: 'desktop-dashboard',
        url: '/',
        findings: [],
        hardFindings: [],
        warnings: [],
        diff: { ratio: 0, sizeMismatch: false },
        metrics: {
            horizontalOverflow: { offenders: [] },
            spacing: {},
            layoutShift: { value: 0 },
        },
    }, {
        summaryPath: '/tmp/ui-lint-summary.json',
        visualRegressionEnabled: false,
    });

    assert.equal(output.duplicateRequests, 0);
    assert.deepEqual(output.badResponseDetails, []);
    assert.deepEqual(output.failedRequestDetails, []);
});

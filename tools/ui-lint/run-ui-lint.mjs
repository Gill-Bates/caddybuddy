//
// tools/ui-lint/run-ui-lint.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import { randomBytes, randomUUID } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { injectAnalyzers, installAnalyzers } from './lib/inject-analyzers.mjs';
import { createDevicePagePool } from './lib/device-page-pool.mjs';
import { serializeResultForOutput } from './lib/result-serializer.mjs';

import { chromium, devices } from 'playwright';

import {
    FULL_MOTION_RESET_CSS,
    LOGIN_ERROR_SETTLE_MS,
    LOGIN_LOCKOUT_RESET_MS,
    LOGIN_TEST_STAGGER_MS,
    SCREENSHOT_SETTLE_MS,
    TAB_SWITCH_SETTLE_MS,
    UI_EVAL_CONSTANTS,
} from './lib/constants.mjs';
import {
    captureKpiCards,
    captureStablePair,
    collectConsoleAndNetwork,
    diffScreenshots,
    disableMotion,
    installLayoutShiftObserver,
    login,
    applyTheme,
    resetLayoutShiftMetric,
    sanitize,
} from './lib/browser-utils.mjs';
import { summarizeFindings, isExpectedStatusUnavailable } from './lib/findings.mjs';
import { LOGIN_FAILURE_VIEWS, VIEWS } from './lib/views.mjs';
import {
    captureVisualSnapshot,
    compareVisualSnapshot,
    ensureVisualRegressionDirs,
    getVisualRegressionConfig,
} from './visual-regression.mjs';

function normalizeBaseUrl(rawValue) {
    let url;
    try {
        url = new URL(rawValue);
    } catch (error) {
        throw new Error(`Invalid UI_LINT_BASE_URL: ${rawValue}`, { cause: error });
    }

    if (!['http:', 'https:'].includes(url.protocol)) {
        throw new Error(`UI_LINT_BASE_URL must use http or https: ${rawValue}`);
    }

    url.hash = '';
    url.search = '';
    return url.toString().replace(/\/$/, '');
}

const BASE_URL = normalizeBaseUrl(process.env.UI_LINT_BASE_URL || 'http://localhost:8000');
const CREDENTIALS_FILE = process.env.UI_LINT_CREDENTIALS_FILE;
const RATE_LIMIT_USERNAME = process.env.UI_LINT_RATE_LIMIT_USERNAME || `ui_lint_rate_limit_${randomUUID()}`;
let cachedCredentials = null;


async function loadCredentials() {
    if (cachedCredentials) {
        return cachedCredentials;
    }
    if (!CREDENTIALS_FILE) {
        throw new Error('UI_LINT_CREDENTIALS_FILE must be set to a private JSON file containing username and password.');
    }

    const resolvedPath = path.resolve(CREDENTIALS_FILE);
    const stats = await fs.stat(resolvedPath);
    if (!stats.isFile()) {
        throw new Error(`UI lint credentials path must be a file: ${resolvedPath}`);
    }
    if ((stats.mode & 0o077) !== 0) {
        throw new Error(`UI lint credentials file must not be group/world accessible: ${resolvedPath}`);
    }

    let parsed;
    try {
        parsed = JSON.parse(await fs.readFile(resolvedPath, 'utf8'));
    } catch (error) {
        throw new Error(`Failed to read UI lint credentials file: ${resolvedPath}`, { cause: error });
    }

    if (!parsed || typeof parsed.username !== 'string' || typeof parsed.password !== 'string') {
        throw new Error('UI lint credentials file must contain JSON with string properties: username, password');
    }

    cachedCredentials = Object.freeze({
        username: parsed.username,
        password: parsed.password,
    });
    return cachedCredentials;
}


const credentialProvider = {
    async getCredentials() {
        return loadCredentials();
    },
    async getUsername() {
        return (await loadCredentials()).username;
    },
    async getPassword() {
        return (await loadCredentials()).password;
    },
};

// Centralized selector registry (reduces duplication & fragility)
const SELECTORS = {
    interactive: 'button, [role="button"], a[href], input:not([type="hidden"]), select, textarea',
    clickTarget: [
        'button',
        '.btn',
        '[role="button"]',
        'a[href]',
        'summary',
        'input[type="button"]',
        'input[type="submit"]',
        'input[type="reset"]',
        'select.form-select:not(.form-select-sm)',
    ].join(', '),
    focusable: [
        'a[href]',
        'button',
        'input:not([type="hidden"])',
        'select',
        'textarea',
        'summary',
        '[tabindex]',
    ].join(', '),
};

if (!CREDENTIALS_FILE) {
    console.error('Error: UI_LINT_CREDENTIALS_FILE must be set');
    console.error('Example: export UI_LINT_CREDENTIALS_FILE=/secure/path/ui-lint-credentials.json');
    process.exit(1);
}

// Generate unique session ID for this test run to avoid conflicts
const SESSION_ID = randomUUID();
const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = process.env.UI_LINT_OUTPUT_DIR || `/tmp/caddybuddy-ui-lint-${SESSION_ID}`;
const SCREENSHOT_DIR = path.resolve(
    OUTPUT_DIR,
    process.env.UI_LINT_SCREENSHOT_DIR || 'screenshots'
);
const RESULTS_DIR = OUTPUT_DIR;
const VISUAL_REGRESSION = getVisualRegressionConfig();

const UI_LINT_FIXED_NOW_ISO = process.env.UI_LINT_VISUAL_FIXED_NOW || '2026-05-01T12:00:00Z';
const UI_LINT_LOCALE = process.env.UI_LINT_LOCALE || 'en-US';
const UI_LINT_TIMEZONE = process.env.UI_LINT_TIMEZONE || 'UTC';
const UI_LINT_EXPECTED_CHROMIUM_MAJOR = process.env.UI_LINT_EXPECTED_CHROMIUM_MAJOR || '';
const UI_LINT_BASELINE_GC = (process.env.UI_LINT_BASELINE_GC || 'warn').toLowerCase();
const UI_LINT_DISABLE_RAF = process.env.UI_LINT_DISABLE_RAF === '1';
const UI_LINT_DISABLE_RESIZE_OBSERVER = process.env.UI_LINT_DISABLE_RESIZE_OBSERVER === '1';
const UI_LINT_VIEW_RETRIES = Math.max(1, Number.parseInt(process.env.UI_LINT_VIEW_RETRIES || '2', 10) || 2);
const FAIL_THRESHOLD = Number.isFinite(Number.parseFloat(process.env.UI_LINT_FAIL_THRESHOLD || '65'))
    ? Number.parseFloat(process.env.UI_LINT_FAIL_THRESHOLD || '65')
    : 65;
const REQUIRED_SECURITY_HEADERS = [
    'content-security-policy',
    'x-frame-options',
    'x-content-type-options',
];

const DEVICE_CONTEXT_OPTIONS = new Map([
    ['desktop', { viewport: { width: 1440, height: 1100 } }],
    ['large-desktop', { viewport: { width: 1600, height: 1100 } }],
    ['tablet', { ...devices['iPad Pro 11'], deviceScaleFactor: 1 }],
    ['mobile', { ...devices['iPhone 13'], deviceScaleFactor: 1 }],
]);

async function installUiLintInitScript(context) {
    await context.addInitScript(({ disableRaf, disableResizeObserver, evalConstants, fixedNowIso, selectors }) => {
        window.__UI_LINT__ = true;
        window.__uiLintRuntimeConfig = {
            constants: evalConstants,
            selectors,
        };

        const fixedNow = new Date(fixedNowIso).valueOf();
        if (Number.isFinite(fixedNow)) {
            const NativeDate = Date;
            class FixedDate extends NativeDate {
                constructor(...args) {
                    if (args.length === 0) {
                        super(fixedNow);
                    } else {
                        super(...args);
                    }
                }

                static now() {
                    return fixedNow;
                }
            }

            Object.defineProperty(FixedDate, 'name', { value: 'Date' });
            window.Date = FixedDate;
        }

        if (!disableRaf) {
            const setTimer = window.setTimeout.bind(window);
            const clearTimer = window.clearTimeout.bind(window);
            window.requestAnimationFrame = (cb) => setTimer(() => cb(performance.now()), 16);
            window.cancelAnimationFrame = (id) => {
                clearTimer(id);
            };
        }

        if (disableResizeObserver) {
            window.ResizeObserver = class {
                observe() { }
                unobserve() { }
                disconnect() { }
            };
        }
    }, {
        disableRaf: UI_LINT_DISABLE_RAF,
        disableResizeObserver: UI_LINT_DISABLE_RESIZE_OBSERVER,
        evalConstants: UI_EVAL_CONSTANTS,
        fixedNowIso: UI_LINT_FIXED_NOW_ISO,
        selectors: SELECTORS,
    });
}

function getNavigationWaitUntil(view) {
    // Live views can keep long-lived connections open, so networkidle may never settle.
    if (view?.live === true) {
        return 'domcontentloaded';
    }
    return 'load';
}

function buildContextOptions(overrides = {}) {
    return {
        locale: UI_LINT_LOCALE,
        timezoneId: UI_LINT_TIMEZONE,
        deviceScaleFactor: 1,
        ...overrides,
    };
}

async function pathExists(targetPath) {
    try {
        await fs.access(targetPath);
        return true;
    } catch {
        return false;
    }
}


function assertSafeGeneratedOutputDir(targetPath, label) {
    const resolved = path.resolve(targetPath);
    const allowedTempRoots = ['/tmp', '/var/tmp'].map((root) => path.resolve(root));
    const isExactTempRoot = allowedTempRoots.includes(resolved);
    const isUnderTempRoot = allowedTempRoots.some((root) => resolved.startsWith(`${root}${path.sep}`));
    const hasExpectedName = path.basename(resolved).startsWith('caddybuddy-ui-lint-');

    if (isExactTempRoot) {
        throw new Error(`Refusing to clean unsafe ${label}: ${resolved}`);
    }

    if (!(isUnderTempRoot && hasExpectedName) && process.env.UI_LINT_FORCE_OUTPUT !== '1') {
        throw new Error(
            `Refusing to clean unsafe ${label}: ${resolved}. Use UI_LINT_FORCE_OUTPUT=1 to override.`,
        );
    }

    return resolved;
}


function assertManagedVisualDir(targetPath, label) {
    const resolved = path.resolve(targetPath);
    const managedRoot = path.resolve(path.join(SCRIPT_DIR, 'visual'));
    const relative = path.relative(managedRoot, resolved);
    const expectedNames = new Set(['current', 'diff']);

    if (relative.startsWith('..') || path.isAbsolute(relative) || !expectedNames.has(path.basename(resolved))) {
        throw new Error(`Refusing to clean unsafe ${label}: ${resolved}`);
    }

    return resolved;
}


function assertPathWithinParent(targetPath, parentPath, label) {
    const resolvedTarget = path.resolve(targetPath);
    const resolvedParent = path.resolve(parentPath);
    const relative = path.relative(resolvedParent, resolvedTarget);
    if (relative.startsWith('..') || path.isAbsolute(relative)) {
        throw new Error(`${label} must be within ${resolvedParent}: ${resolvedTarget}`);
    }
    return resolvedTarget;
}

function groupViewsByDevice(views) {
    const groups = new Map();
    for (const view of views) {
        const device = DEVICE_CONTEXT_OPTIONS.has(view.device) ? view.device : 'desktop';
        const group = groups.get(device) || [];
        group.push(view);
        groups.set(device, group);
    }
    return groups;
}

function buildErrorResult(view, error, context = {}) {
    const message = error instanceof Error ? error.message : String(error);
    return {
        name: view.name,
        url: view.url,
        theme: view.theme,
        error: {
            device: context.device ?? view.device ?? null,
            message,
            phase: context.phase ?? 'unknown',
            stack: error instanceof Error ? (error.stack || null) : null,
        },
        findings: ['auditError'],
        hardFindings: ['auditError'],
        warnings: [],
        diff: { ratio: 0, sizeMismatch: false },
        metrics: {},
        network: {
            consoleEntries: [],
            pageErrors: [],
            requestFailures: [],
            badResponses: [],
            requests: [],
            duplicateRequests: [],
        },
        securityHeaders: { missing: [] },
        visualRegression: null,
    };
}

function applySummary(result) {
    const summarized = summarizeFindings(result);
    result.hardFindings = summarized.hardFindings;
    result.warnings = summarized.warnings;

    const missingSecurityHeaders = result.securityHeaders?.missing || [];
    if (missingSecurityHeaders.length) {
        result.hardFindings = [
            ...result.hardFindings,
            `missingSecurityHeaders=${missingSecurityHeaders.join(',')}`,
        ];
    }

    result.findings = [
        ...result.hardFindings,
        ...(result.warnings || []),
    ];

    return result;
}

function requiredSecurityHeadersForResponse(response) {
    const requiredHeaders = [...REQUIRED_SECURITY_HEADERS];
    try {
        if (response && new URL(response.url()).protocol === 'https:') {
            requiredHeaders.push('strict-transport-security');
        }
    } catch {
        // Keep base security headers for malformed or absent response URLs.
    }
    return requiredHeaders;
}

function collectSecurityHeaders(response) {
    const headers = response?.headers?.() || {};
    const requiredHeaders = response ? requiredSecurityHeadersForResponse(response) : REQUIRED_SECURITY_HEADERS;
    return {
        missing: requiredHeaders.filter((header) => !headers[header]),
    };
}

async function prepareOutputDirs() {
    const resolvedOutputDir = assertSafeGeneratedOutputDir(OUTPUT_DIR, 'UI lint output directory');
    const resolvedScreenshotDir = assertPathWithinParent(SCREENSHOT_DIR, resolvedOutputDir, 'UI lint screenshot directory');

    await fs.rm(resolvedOutputDir, { recursive: true, force: true });
    await fs.mkdir(resolvedOutputDir, { recursive: true, mode: 0o700 });
    await fs.mkdir(resolvedScreenshotDir, { recursive: true, mode: 0o700 });

    if (VISUAL_REGRESSION.enabled) {
        const currentDir = assertManagedVisualDir(VISUAL_REGRESSION.currentDir, 'visual regression current directory');
        const diffDir = assertManagedVisualDir(VISUAL_REGRESSION.diffDir, 'visual regression diff directory');

        await fs.rm(currentDir, { recursive: true, force: true });
        await fs.rm(diffDir, { recursive: true, force: true });
        ensureVisualRegressionDirs(VISUAL_REGRESSION);
    }
}

async function runBaselineGc(config, expectedSnapshotNames) {
    if (!config.enabled || !(await pathExists(config.baselineDir))) {
        return;
    }

    const expected = new Set(expectedSnapshotNames.map((name) => `${sanitize(name)}.png`));
    const baselineFiles = (await fs.readdir(config.baselineDir)).filter((name) => name.endsWith('.png'));
    const obsolete = baselineFiles.filter((file) => !expected.has(file));

    if (!obsolete.length) {
        return;
    }

    if (UI_LINT_BASELINE_GC === 'delete') {
        await Promise.all(obsolete.map((file) => fs.rm(path.join(config.baselineDir, file), { force: true })));
        console.log(`Baseline GC removed obsolete files: ${obsolete.join(', ')}`);
        return;
    }

    console.warn(`Baseline GC warning (obsolete baselines): ${obsolete.join(', ')}`);
}

async function collectPageMetrics(page, scope) {
    await injectAnalyzers(page);

    return page.evaluate(async (currentScope) => {
        const runtimeConfig = window.__uiLintRuntimeConfig || {};
        return window.__uiLint.runAll({
            scope: currentScope,
            constants: runtimeConfig.constants || {},
            selectors: runtimeConfig.selectors || {},
        });
    }, scope);
}

function mergeMetricsPatch(metrics, patch) {
    if (!patch || typeof patch !== 'object') {
        return metrics;
    }

    for (const [key, value] of Object.entries(patch)) {
        if (
            value
            && typeof value === 'object'
            && !Array.isArray(value)
            && metrics[key]
            && typeof metrics[key] === 'object'
            && !Array.isArray(metrics[key])
        ) {
            metrics[key] = { ...metrics[key], ...value };
            continue;
        }
        metrics[key] = value;
    }

    return metrics;
}

function stripAbortedRequests(network) {
    network.requestFailures = network.requestFailures.filter((entry) => entry.error !== 'net::ERR_ABORTED');
}

function logAuditError(prefix, error) {
    console.error(prefix);
    console.error(error);
}

async function withRetry(action, {
    attempts = UI_LINT_VIEW_RETRIES,
    label = 'operation',
    onRetry,
    shouldRetry = () => true,
} = {}) {
    let lastError = null;

    for (let attempt = 1; attempt <= attempts; attempt += 1) {
        try {
            return await action(attempt);
        } catch (error) {
            lastError = error;
            if (attempt >= attempts || !shouldRetry(error)) {
                break;
            }
            if (onRetry) {
                await onRetry({ attempt, error, label, remaining: attempts - attempt });
            }
        }
    }

    throw lastError;
}

async function captureArtifacts(page, view) {
    const shots = await captureStablePair(page, {
        motionResetCss: FULL_MOTION_RESET_CSS,
        name: view.name,
        screenshotDir: SCREENSHOT_DIR,
        screenshotSettleMs: SCREENSHOT_SETTLE_MS,
    });
    const kpiShots = await captureKpiCards(page, view.name, SCREENSHOT_DIR);
    const diff = await diffScreenshots({
        name: view.name,
        shotA: shots.shotA,
        shotB: shots.shotB,
        screenshotDir: SCREENSHOT_DIR,
    });

    let visualRegression = null;
    if (VISUAL_REGRESSION.enabled) {
        await captureVisualSnapshot(page, view.name, VISUAL_REGRESSION);
        visualRegression = compareVisualSnapshot(view.name, VISUAL_REGRESSION);
    }

    return {
        diff,
        kpiShots,
        screenshots: { ...shots, diffPath: diff.diffPath },
        visualRegression,
    };
}

async function collectViewMetrics(page, view, metricsPatch = null) {
    const metrics = await collectPageMetrics(page, view.scope);
    if (!metrics) {
        throw new Error(`No metrics collected for view: ${view.name}`);
    }

    return mergeMetricsPatch(metrics, metricsPatch);
}

async function collectAuditArtifacts(page, view, metricsPatch = null) {
    await resetLayoutShiftMetric(page);
    await page.waitForTimeout(SCREENSHOT_SETTLE_MS);

    const artifactBundle = await captureArtifacts(page, view);
    const metrics = await collectViewMetrics(page, view, metricsPatch);

    return {
        ...artifactBundle,
        metrics,
    };
}

async function auditPageFlow(page, view, {
    load,
    afterLoad,
    prepare,
    finalize,
} = {}) {
    const detachNetwork = collectConsoleAndNetwork(page);
    let network = null;
    try {
        const response = await load(page, view);
        await disableMotion(page, FULL_MOTION_RESET_CSS, view.name);
        if (afterLoad) {
            await afterLoad({ page, view, response });
        }

        const prepared = prepare ? await prepare({ page, view, response }) : {};
        if (view.tab) {
            await page.locator(view.tab).first().click();
            await page.waitForTimeout(TAB_SWITCH_SETTLE_MS);
        }
        const artifacts = await collectAuditArtifacts(page, view, prepared.metricsPatch || null);
        network = detachNetwork();
        stripAbortedRequests(network);

        let resultFields = prepared.resultFields || {};
        let securityResponse = prepared.securityResponse || response;
        if (finalize) {
            const finalized = await finalize({
                network,
                page,
                prepared,
                response,
                view,
            }) || {};
            if (finalized.network) {
                network = finalized.network;
            }
            if (finalized.resultFields) {
                resultFields = { ...resultFields, ...finalized.resultFields };
            }
            if (finalized.securityResponse) {
                securityResponse = finalized.securityResponse;
            }
        }

        return {
            name: view.name,
            url: page.url(),
            theme: view.theme,
            diff: artifacts.diff,
            metrics: artifacts.metrics,
            network,
            securityHeaders: collectSecurityHeaders(securityResponse),
            findings: [],
            screenshots: artifacts.screenshots,
            visualRegression: artifacts.visualRegression,
            kpiShots: artifacts.kpiShots,
            ...resultFields,
        };
    } finally {
        // Always detach network listeners to prevent memory leaks
        if (!network) detachNetwork();
    }
}

async function waitForLoginFailureUi(page) {
    await page.locator('.alert[role="alert"]').first().waitFor({ state: 'visible', timeout: 30000 });
    await page.waitForFunction(() => {
        const alert = document.querySelector('.alert[role="alert"]');
        const submitButton = document.querySelector('form[action="/login"] button[type="submit"]');
        if (!alert || !submitButton) {
            return false;
        }
        const style = window.getComputedStyle(alert);
        const rect = alert.getBoundingClientRect();
        return style.display !== 'none'
            && style.visibility !== 'hidden'
            && rect.width > 0
            && rect.height > 0
            && submitButton.disabled === false;
    }, { timeout: Math.max(LOGIN_ERROR_SETTLE_MS, 3000) });
}

async function auditView(page, view) {
    return auditPageFlow(page, view, {
        load: async () => {
            await applyTheme(page, { baseUrl: BASE_URL, theme: view.theme, label: view.name });
            return page.goto(`${BASE_URL}${view.url}`, {
                waitUntil: getNavigationWaitUntil(view),
                timeout: 30000,
            });
        },
        finalize: async ({ network, response, view }) => {
            const statusUnavailableExpected = isExpectedStatusUnavailable(view, response);
            if (statusUnavailableExpected) {
                network.consoleEntries = network.consoleEntries.filter((entry) => {
                    const text = String(entry.text || '');
                    return !(text.includes('/status') && text.includes('404'))
                        && text !== 'Failed to load resource: the server responded with a status of 404 (Not Found)';
                });
                network.badResponses = network.badResponses.filter((entry) => {
                    try {
                        return !(entry.status === 404 && new URL(entry.url).pathname === '/status');
                    } catch {
                        return true;
                    }
                });
            }

            return { network, resultFields: { statusUnavailableExpected } };
        },
    });
}

async function auditLoginFailureView(page, view) {
    return auditPageFlow(page, view, {
        load: () => page.goto(`${BASE_URL}${view.url}`, { waitUntil: 'domcontentloaded', timeout: 10000 }),
        afterLoad: () => applyTheme(page, { baseUrl: BASE_URL, theme: view.theme, label: view.name }),
        prepare: async () => {
            const { username } = await credentialProvider.getCredentials();
            const invalidPassword = randomBytes(24).toString('hex');
            await page.fill('#username', username);
            await page.fill('#password', invalidPassword);

            const [loginResponse] = await Promise.all([
                page.waitForResponse((response) => {
                    try {
                        return new URL(response.url()).pathname === '/login' && response.request().method() === 'POST';
                    } catch {
                        return false;
                    }
                }, { timeout: 30000 }),
                page.locator('form[action="/login"] button[type="submit"]').first().click(),
            ]);

            await page.waitForURL((url) => {
                try {
                    return url.pathname === '/login';
                } catch {
                    return false;
                }
            }, { timeout: 30000 }).catch(() => { });
            await waitForLoginFailureUi(page);

            const loginFailure = await page.evaluate(() => {
                const isVisible = (element) => {
                    if (!element || !element.isConnected) return false;
                    if (element.closest('.d-none, [hidden], [aria-hidden="true"]')) return false;
                    const style = window.getComputedStyle(element);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };

                const alert = Array.from(document.querySelectorAll('.alert[role="alert"]')).find((element) => isVisible(element)) || null;
                const submitButton = document.querySelector('form[action="/login"] button[type="submit"]');
                const passwordInput = document.getElementById('password');

                return {
                    alertVisible: isVisible(alert),
                    errorText: alert?.textContent?.trim() || '',
                    submitButtonDisabled: Boolean(submitButton?.disabled),
                    submitButtonReset: !submitButton?.disabled,
                    submitButtonLabel: submitButton?.textContent?.trim() || '',
                    passwordInvalidClass: Boolean(passwordInput?.classList.contains('is-invalid')),
                };
            });

            return {
                metricsPatch: { loginFailure },
                resultFields: { loginResponseStatus: loginResponse.status() },
                securityResponse: loginResponse,
                loginResponse,
            };
        },
        finalize: async ({ network, prepared }) => {
            const { loginResponse } = prepared;
            network.consoleEntries = network.consoleEntries.filter((entry) =>
                !(loginResponse.status() === 401 && entry.text.includes('401 (Unauthorized)'))
            );
            network.badResponses = network.badResponses.filter((entry) => {
                try {
                    return !(entry.status === 401 && new URL(entry.url).pathname === '/login');
                } catch {
                    return true;
                }
            });

            return {
                network,
                resultFields: prepared.resultFields,
                securityResponse: prepared.securityResponse,
            };
        },
    });
}

function buildLoginRateLimitResult({ attempts, response, reached429 }) {
    const status = response?.status?.() ?? null;
    const redirectedWithout429 = status >= 300 && status < 400 && status !== 429;
    const rateLimitHandled = reached429 || status === 429;
    const result = {
        name: 'login-rate-limit',
        url: `${BASE_URL}/login`,
        theme: null,
        diff: { ratio: 0, sizeMismatch: false },
        metrics: {
            loginRateLimit: {
                attempts,
                reached429,
                status,
                redirectedWithout429,
            },
        },
        network: {
            consoleEntries: [],
            pageErrors: [],
            requestFailures: [],
            badResponses: [],
            requests: [],
            duplicateRequests: [],
        },
        securityHeaders: collectSecurityHeaders(response),
        findings: [],
        hardFindings: [],
        warnings: [],
        visualRegression: null,
    };

    applySummary(result);
    if (!rateLimitHandled) {
        result.hardFindings = [...result.hardFindings, 'loginRateLimitMissing429'];
        result.findings = [...result.hardFindings, ...(result.warnings || [])];
    }

    return result;
}

async function auditLoginRateLimit(browser) {
    const context = await browser.newContext(buildContextOptions(DEVICE_CONTEXT_OPTIONS.get('desktop')));
    try {
        const page = await context.newPage();
        let attempts = 0;
        let lastResponse = null;

        for (; attempts < 6; attempts += 1) {
            await page.goto(`${BASE_URL}/login`, { waitUntil: 'domcontentloaded', timeout: 10000 });
            await page.fill('#username', RATE_LIMIT_USERNAME);
            await page.fill('#password', randomBytes(24).toString('hex'));

            const [response] = await Promise.all([
                page.waitForResponse((candidate) => {
                    try {
                        return new URL(candidate.url()).pathname === '/login' && candidate.request().method() === 'POST';
                    } catch {
                        return false;
                    }
                }, { timeout: 30000 }),
                page.locator('form[action="/login"] button[type="submit"]').first().click(),
            ]);

            lastResponse = response;
            if (response.status() === 429) {
                return buildLoginRateLimitResult({
                    attempts: attempts + 1,
                    response,
                    reached429: true,
                });
            }
        }

        return buildLoginRateLimitResult({
            attempts,
            response: lastResponse,
            reached429: false,
        });
    } finally {
        await context.close();
    }
}

async function runAuthenticatedViews(pagePool) {
    const groupedViews = groupViewsByDevice(VIEWS);
    const settled = await Promise.all(
        Array.from(groupedViews.entries()).map(async ([device, views]) => {
            let page;
            try {
                page = await pagePool.getPage(device);
            } catch (error) {
                logAuditError(`[${device}] Page setup failed`, error);
                return views.map((view) => buildErrorResult(view, error, { device, phase: 'page-setup' }));
            }

            const results = [];
            for (const view of views) {
                try {
                    const result = await withRetry(
                        () => auditView(page, view),
                        {
                            label: view.name,
                            onRetry: ({ attempt, error, remaining }) => {
                                console.warn(
                                    `[${view.name}] Audit attempt ${attempt} failed; retrying (${remaining} remaining): ${error instanceof Error ? error.message : String(error)}`,
                                );
                            },
                        },
                    );
                    results.push(applySummary(result));
                } catch (error) {
                    logAuditError(`[${view.name}] Audit failed`, error);
                    results.push(buildErrorResult(view, error, { device, phase: 'audit-view' }));
                }
            }
            return results;
        })
    );

    return settled.flat();
}

async function runLoginFailureViews(pagePool) {
    const groupedViews = groupViewsByDevice(LOGIN_FAILURE_VIEWS);
    const results = [];

    for (const [device, views] of groupedViews.entries()) {
        let page;
        try {
            page = await pagePool.getPage(device);
        } catch (error) {
            logAuditError(`[${device}] Login-failure page setup failed`, error);
            results.push(...views.map((view) => buildErrorResult(view, error, { device, phase: 'login-page-setup' })));
            continue;
        }

        for (const [index, view] of views.entries()) {
            let result;
            let attempt = 0;
            const maxRetries = 3;

            while (attempt < maxRetries) {
                try {
                    if (index > 0 && attempt === 0) {
                        await new Promise((resolve) => setTimeout(resolve, LOGIN_TEST_STAGGER_MS));
                    }

                    result = await auditLoginFailureView(page, view);

                    const errorText = result?.metrics?.loginFailure?.errorText?.toLowerCase() || '';
                    if (errorText.includes('too many') || errorText.includes('rate limit') || errorText.includes('locked')) {
                        if (attempt < maxRetries - 1) {
                            console.warn(`[${view.name}] Rate limited, waiting ${LOGIN_LOCKOUT_RESET_MS}ms before retry ${attempt + 1}/${maxRetries - 1}`);
                            await new Promise((resolve) => setTimeout(resolve, LOGIN_LOCKOUT_RESET_MS));
                            attempt += 1;
                            continue;
                        }
                    }

                    break;
                } catch (error) {
                    if (attempt === maxRetries - 1) {
                        logAuditError(`[${view.name}] Audit failed after ${maxRetries} attempts`, error);
                        result = buildErrorResult(view, error, { device, phase: 'audit-login-failure' });
                        break;
                    }
                    attempt += 1;
                }
            }

            if (result) {
                results.push(applySummary(result));
            }
        }
    }

    return results;
}

function emitResults(results, summaryPath) {
    console.log(`\nResults saved to: ${summaryPath}`);
    console.log(`Screenshots: ${SCREENSHOT_DIR}\n`);

    console.log('UI_LINT_START');
    for (const result of results) {
        console.log(JSON.stringify(serializeResultForOutput(result, {
            summaryPath,
            visualRegressionEnabled: VISUAL_REGRESSION.enabled,
        })));
    }
    console.log('UI_LINT_END');
}

async function main() {
    await loadCredentials();

    await prepareOutputDirs();

    const results = [];
    let browser;
    let authPagePool;
    let loginFailurePagePool;

    try {
        browser = await chromium.launch({ headless: true });
        const browserVersion = browser.version();
        if (UI_LINT_EXPECTED_CHROMIUM_MAJOR && !browserVersion.startsWith(`${UI_LINT_EXPECTED_CHROMIUM_MAJOR}.`)) {
            console.warn(`Chromium version drift: expected major ${UI_LINT_EXPECTED_CHROMIUM_MAJOR}, got ${browserVersion}`);
        }

        const authContext = await browser.newContext(buildContextOptions(DEVICE_CONTEXT_OPTIONS.get('desktop')));
        const authPage = await authContext.newPage();
        await authPage.emulateMedia({ reducedMotion: 'reduce' });
        await login(authPage, {
            baseUrl: BASE_URL,
            credentialProvider,
            motionResetCss: FULL_MOTION_RESET_CSS,
        });
        const authState = await authContext.storageState();
        await authContext.close();

        authPagePool = createDevicePagePool({
            browser,
            buildContextOptions,
            deviceContextOptions: DEVICE_CONTEXT_OPTIONS,
            installAnalyzers,
            installLayoutShiftObserver,
            installUiLintInitScript,
            storageState: authState,
        });
        loginFailurePagePool = createDevicePagePool({
            browser,
            buildContextOptions,
            deviceContextOptions: DEVICE_CONTEXT_OPTIONS,
            installAnalyzers,
            installLayoutShiftObserver,
            installUiLintInitScript,
        });

        results.push(...await runAuthenticatedViews(authPagePool));
        results.push(...await runLoginFailureViews(loginFailurePagePool));

        results.push(await auditLoginRateLimit(browser));

        await runBaselineGc(
            VISUAL_REGRESSION,
            [...VIEWS, ...LOGIN_FAILURE_VIEWS].map((view) => view.name)
        );
    } finally {
        if (authPagePool) {
            await authPagePool.closeAll();
        }
        if (loginFailurePagePool) {
            await loginFailurePagePool.closeAll();
        }
        if (browser) {
            await browser.close();
        }
    }

    await fs.mkdir(RESULTS_DIR, { recursive: true, mode: 0o700 });
    const summaryPath = path.join(RESULTS_DIR, 'ui-lint-summary.json');
    await fs.writeFile(summaryPath, JSON.stringify(results, null, 2));
    emitResults(results, summaryPath);

    const hasHardFindings = results.some((result) => (result.hardFindings || []).length > 0);
    const hasVisualRegressionFailures = VISUAL_REGRESSION.enabled &&
        results.some((result) => result.visualRegression && result.visualRegression.pass === false);
    const lowScoreResults = results.filter((result) => Number(result.metrics?.uiScore ?? 100) < FAIL_THRESHOLD);
    if (hasVisualRegressionFailures) {
        const failed = results
            .filter((result) => result.visualRegression && result.visualRegression.pass === false)
            .map((result) => `${result.name}:${result.visualRegression.reason}`)
            .join(', ');
        console.error(`Visual regression failed: ${failed}`);
    }
    if (lowScoreResults.length) {
        console.error(`UI score below threshold (${FAIL_THRESHOLD}): ${lowScoreResults.map((result) => `${result.name}=${result.metrics.uiScore}`).join(', ')}`);
    }
    // Exit codes:
    //   0 - all checks passed
    //   1 - hard findings present without score/regression failure
    //   2 - visual regression failure or UI score below FAIL_THRESHOLD
    process.exitCode = (lowScoreResults.length || hasVisualRegressionFailures) ? 2 : (hasHardFindings ? 1 : 0);
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});

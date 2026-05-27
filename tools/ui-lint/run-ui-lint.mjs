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

import { chromium, firefox, webkit, devices } from 'playwright';

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
        throw new Error('Invalid UI_LINT_BASE_URL.', { cause: error });
    }

    if (!['http:', 'https:'].includes(url.protocol)) {
        throw new Error('UI_LINT_BASE_URL must use http or https.');
    }

    if (url.username || url.password) {
        throw new Error('UI_LINT_BASE_URL must not include username or password.');
    }

    url.hash = '';
    url.search = '';
    return url.toString().replace(/\/$/, '');
}

function parseBrowserTypes() {
    const requested = (process.env.UI_LINT_BROWSERS || 'chromium')
        .split(',')
        .map((value) => value.trim().toLowerCase())
        .filter(Boolean);

    const supported = new Set(['chromium', 'firefox', 'webkit']);
    const invalid = requested.filter((name) => !supported.has(name));

    if (invalid.length) {
        throw new Error(`Unsupported UI_LINT_BROWSERS value(s): ${invalid.join(', ')}`);
    }

    if (!requested.length) {
        throw new Error('UI_LINT_BROWSERS must contain at least one browser.');
    }

    return requested;
}

function parsePositiveIntegerEnv(rawValue, fallback, label) {
    const value = Number.parseInt(String(rawValue ?? fallback), 10);
    if (!Number.isFinite(value) || value <= 0) {
        throw new Error(`${label} must be a positive integer.`);
    }
    return value;
}

function parseBoundedFloatEnv(rawValue, fallback, label, min, max) {
    const value = Number.parseFloat(String(rawValue ?? fallback));
    if (!Number.isFinite(value) || value < min || value > max) {
        throw new Error(`${label} must be a number between ${min} and ${max}.`);
    }
    return value;
}

const BASE_URL = normalizeBaseUrl(process.env.UI_LINT_BASE_URL || 'http://localhost:8000');
const CREDENTIALS_FILE = process.env.UI_LINT_CREDENTIALS_FILE;
const SESSION_ID = randomUUID();
const RATE_LIMIT_USERNAME = process.env.UI_LINT_RATE_LIMIT_USERNAME || `ui_lint_rate_limit_${SESSION_ID}`;
const LOGIN_FAILURE_USERNAME = process.env.UI_LINT_LOGIN_FAILURE_USERNAME || `ui_lint_invalid_${SESSION_ID}`;
let cachedCredentials = null;


async function loadCredentials() {
    if (cachedCredentials) {
        return cachedCredentials;
    }

    if (process.env.UI_LINT_USERNAME && process.env.UI_LINT_PASSWORD) {
        if (!process.env.CI) {
            console.warn('Warning: using UI lint credentials from environment variables. Prefer UI_LINT_CREDENTIALS_FILE locally.');
        }
        cachedCredentials = Object.freeze({
            username: process.env.UI_LINT_USERNAME,
            password: process.env.UI_LINT_PASSWORD,
        });
        return cachedCredentials;
    }

    if (!CREDENTIALS_FILE) {
        throw new Error('UI_LINT_CREDENTIALS_FILE or UI_LINT_USERNAME/UI_LINT_PASSWORD must be set.');
    }

    const resolvedPath = path.resolve(CREDENTIALS_FILE);
    const linkStats = await fs.lstat(resolvedPath);
    if (linkStats.isSymbolicLink()) {
        throw new Error(`UI lint credentials file must not be a symlink: ${resolvedPath}`);
    }

    const stats = await fs.stat(resolvedPath);
    if (!stats.isFile()) {
        throw new Error(`UI lint credentials path must be a file: ${resolvedPath}`);
    }
    if ((stats.mode & 0o077) !== 0) {
        throw new Error(`UI lint credentials file must not be group/world accessible: ${resolvedPath}`);
    }
    if (typeof process.getuid === 'function' && stats.uid !== process.getuid()) {
        throw new Error(`UI lint credentials file must be owned by the current user: ${resolvedPath}`);
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
        'button:not([data-ui-lint-ignore-click-target])',
        '.btn:not([data-ui-lint-ignore-click-target])',
        '[role="button"]:not([data-ui-lint-ignore-click-target])',
        'a[href]:not([data-ui-lint-ignore-click-target])',
        'summary:not([data-ui-lint-ignore-click-target])',
        'input[type="button"]:not([data-ui-lint-ignore-click-target])',
        'input[type="submit"]:not([data-ui-lint-ignore-click-target])',
        'input[type="reset"]:not([data-ui-lint-ignore-click-target])',
        'select.form-select:not(.form-select-sm):not([data-ui-lint-ignore-click-target])',
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

if (!CREDENTIALS_FILE && (!process.env.UI_LINT_USERNAME || !process.env.UI_LINT_PASSWORD)) {
    console.error('Error: UI_LINT_CREDENTIALS_FILE or UI_LINT_USERNAME/UI_LINT_PASSWORD must be set');
    process.exit(1);
}

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const OUTPUT_DIR = process.env.UI_LINT_OUTPUT_DIR || path.join(SCRIPT_DIR, 'test-results');
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
const UI_LINT_MOCK_RAF = process.env.UI_LINT_MOCK_RAF != null
    ? process.env.UI_LINT_MOCK_RAF === '1'
    : process.env.UI_LINT_DISABLE_RAF !== '1';
const UI_LINT_DISABLE_RESIZE_OBSERVER = process.env.UI_LINT_DISABLE_RESIZE_OBSERVER === '1';
const UI_LINT_VIEW_RETRIES = Math.max(1, Number.parseInt(process.env.UI_LINT_VIEW_RETRIES || '2', 10) || 2);
const UI_LINT_RATE_LIMIT_ATTEMPTS = Math.max(1, Number.parseInt(process.env.UI_LINT_RATE_LIMIT_ATTEMPTS || '6', 10) || 6);
const UI_LINT_BROWSER_CONCURRENCY = parsePositiveIntegerEnv(
    process.env.UI_LINT_BROWSER_CONCURRENCY,
    1,
    'UI_LINT_BROWSER_CONCURRENCY',
);
const UI_LINT_DEVICE_CONCURRENCY = parsePositiveIntegerEnv(
    process.env.UI_LINT_DEVICE_CONCURRENCY,
    1,
    'UI_LINT_DEVICE_CONCURRENCY',
);
const FAIL_THRESHOLD = parseBoundedFloatEnv(
    process.env.UI_LINT_FAIL_THRESHOLD,
    65,
    'UI_LINT_FAIL_THRESHOLD',
    0,
    100,
);
const REQUIRED_SECURITY_HEADERS = [
    'content-security-policy',
    'x-frame-options',
    'x-content-type-options',
];
const BASE_URL_CHECK_TIMEOUT_MS = 5000;

const DEVICE_CONTEXT_OPTIONS = new Map([
    ['desktop', { viewport: { width: 1440, height: 1100 } }],
    ['large-desktop', { viewport: { width: 1600, height: 1100 } }],
    ['tablet', { ...devices['iPad Pro 11'] }],
    ['mobile', { ...devices['iPhone 13'] }],
]);

async function installUiLintInitScript(context) {
    await context.addInitScript(({ disableResizeObserver, evalConstants, fixedNowIso, mockRaf, selectors }) => {
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

        if (mockRaf) {
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
        disableResizeObserver: UI_LINT_DISABLE_RESIZE_OBSERVER,
        evalConstants: UI_EVAL_CONSTANTS,
        fixedNowIso: UI_LINT_FIXED_NOW_ISO,
        mockRaf: UI_LINT_MOCK_RAF,
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
        ...overrides,
    };
}

function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function runWithConcurrency(items, concurrency, worker) {
    const queue = [...items];
    const workerCount = Math.max(1, Math.min(concurrency, queue.length || 1));
    const workers = Array.from({ length: workerCount }, async () => {
        while (queue.length > 0) {
            const item = queue.shift();
            if (item === undefined) {
                return;
            }
            await worker(item);
        }
    });

    await Promise.all(workers);
}

async function assertBaseUrlReachable() {
    const loginUrl = new URL('/login', BASE_URL).toString();
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), BASE_URL_CHECK_TIMEOUT_MS);

    try {
        const response = await fetch(loginUrl, {
            method: 'GET',
            redirect: 'manual',
            signal: controller.signal,
        });
        if (response.status >= 300 && response.status < 400) {
            const location = response.headers.get('location');
            if (location) {
                const source = new URL(loginUrl);
                const target = new URL(location, loginUrl);
                if (target.origin !== source.origin) {
                    throw new Error(`Unexpected external redirect from /login to ${target.origin}`);
                }
            }
        }
        if (!response.ok && (response.status < 300 || response.status >= 400)) {
            throw new Error(`HTTP ${response.status} from ${loginUrl}`);
        }
    } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        throw new Error(
            `UI_LINT_BASE_URL is unreachable at ${loginUrl}. Start the app first, for example: cd /opt/caddybuddy && source .venv/bin/activate && python run.py. Underlying error: ${detail}`,
            { cause: error instanceof Error ? error : undefined },
        );
    } finally {
        clearTimeout(timeoutId);
    }
}

async function pathExists(targetPath) {
    try {
        await fs.access(targetPath);
        return true;
    } catch {
        return false;
    }
}

function isPathInside(parentPath, targetPath) {
    const parent = path.resolve(parentPath);
    const target = path.resolve(targetPath);
    const relative = path.relative(parent, target);

    return relative !== ''
        && !relative.startsWith('..')
        && !path.isAbsolute(relative);
}


function assertSafeGeneratedOutputDir(targetPath, label) {
    const resolved = path.resolve(targetPath);
    const expectedProjectResults = path.resolve(path.join(SCRIPT_DIR, 'test-results'));
    const allowedTempRoots = ['/tmp', '/var/tmp'].map((root) => path.resolve(root));
    const isUnderTempRoot = allowedTempRoots.some((root) => isPathInside(root, resolved));
    const hasExpectedName = path.basename(resolved).startsWith('caddybuddy-ui-lint-') || path.basename(resolved) === 'test-results';

    if (resolved === expectedProjectResults) {
        return resolved;
    }

    if (allowedTempRoots.includes(resolved)) {
        throw new Error(`Refusing to clean unsafe ${label}: ${resolved}`);
    }

    if (!(isUnderTempRoot && hasExpectedName)) {
        throw new Error(
            `Refusing to clean unsafe ${label}: ${resolved}.`,
        );
    }

    return resolved;
}


function assertManagedVisualDir(targetPath, label) {
    const resolved = path.resolve(targetPath);
    const managedRoot = path.resolve(path.join(SCRIPT_DIR, 'test-results', 'visual'));
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
    const weakSecurityHeaders = result.securityHeaders?.weak || [];
    if (missingSecurityHeaders.length) {
        result.hardFindings = [
            ...result.hardFindings,
            `missingSecurityHeaders=${missingSecurityHeaders.join(',')}`,
        ];
    }
    if (weakSecurityHeaders.length) {
        result.hardFindings = [
            ...result.hardFindings,
            ...weakSecurityHeaders,
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
    const rawHeaders = response?.headers?.() || {};
    const headers = Object.fromEntries(
        Object.entries(rawHeaders).map(([key, value]) => [String(key).toLowerCase(), value]),
    );
    const requiredHeaders = response ? requiredSecurityHeadersForResponse(response) : REQUIRED_SECURITY_HEADERS;
    const weak = [];

    const csp = String(headers['content-security-policy'] || '');
    if (csp) {
        const requiredDirectives = [
            "default-src 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "base-uri 'self'",
        ];
        for (const directive of requiredDirectives) {
            if (!csp.includes(directive)) {
                weak.push(`weakCspMissing=${directive}`);
            }
        }
    }

    const xFrameOptions = String(headers['x-frame-options'] || '').toLowerCase();
    if (headers['x-frame-options'] && !['deny', 'sameorigin'].includes(xFrameOptions)) {
        weak.push('weakXFrameOptions');
    }

    const xContentTypeOptions = String(headers['x-content-type-options'] || '').toLowerCase();
    if (headers['x-content-type-options'] && xContentTypeOptions !== 'nosniff') {
        weak.push('weakXContentTypeOptions');
    }

    try {
        if (response && new URL(response.url()).protocol === 'https:') {
            const hsts = String(headers['strict-transport-security'] || '');
            if (hsts && !/max-age=\d+/i.test(hsts)) {
                weak.push('missingOrWeakHsts');
            }
        }
    } catch {
        weak.push('securityHeaderUrlParseError');
    }

    return {
        missing: requiredHeaders.filter((header) => !headers[header]),
        weak,
    };
}

async function prepareOutputDirs() {
    const resolvedOutputDir = assertSafeGeneratedOutputDir(OUTPUT_DIR, 'UI lint output directory');
    const resolvedScreenshotDir = assertPathWithinParent(SCREENSHOT_DIR, resolvedOutputDir, 'UI lint screenshot directory');

    if (await pathExists(resolvedOutputDir)) {
        await fs.rm(resolvedOutputDir, { recursive: true, force: true });
    }
    await fs.mkdir(resolvedOutputDir, { recursive: true, mode: 0o700 });
    await fs.mkdir(resolvedScreenshotDir, { recursive: true, mode: 0o700 });

    if (VISUAL_REGRESSION.enabled) {
        const currentDir = assertManagedVisualDir(VISUAL_REGRESSION.currentDir, 'visual regression current directory');
        const diffDir = assertManagedVisualDir(VISUAL_REGRESSION.diffDir, 'visual regression diff directory');

        await fs.rm(currentDir, { recursive: true, force: true });
        await fs.rm(diffDir, { recursive: true, force: true });
        await ensureVisualRegressionDirs(VISUAL_REGRESSION);
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

    assignSafeObject(metrics, patch);

    return metrics;
}

function assignSafeObject(target, source) {
    for (const [key, value] of Object.entries(source || {})) {
        if (key === '__proto__' || key === 'constructor' || key === 'prototype') {
            continue;
        }
        if (
            value
            && typeof value === 'object'
            && !Array.isArray(value)
            && target[key]
            && typeof target[key] === 'object'
            && !Array.isArray(target[key])
        ) {
            assignSafeObject(target[key], value);
            continue;
        }
        target[key] = value;
    }

    return target;
}

function stripAbortedRequests(network) {
    network.requestFailures = network.requestFailures.filter((entry) => entry.error !== 'net::ERR_ABORTED');
}

function logAuditError(prefix, error) {
    console.error(prefix);
    console.error(error);
}

function isLoginRateLimitMessage(value) {
    const message = String(value?.message || value).toLowerCase();
    return message.includes('too many') || message.includes('rate limit') || message.includes('locked');
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
        visualRegression = await compareVisualSnapshot(view.name, VISUAL_REGRESSION);
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
    await page.waitForFunction(() => {
        const normalizeLoginFailureText = (value) => String(value || '').replace(/\s+/g, ' ').trim();
        const extractLoginFailureText = (documentRoot = document) => {
            const candidates = [
                '.app-flash-stack .alert[role="alert"]',
                '.alert[role="alert"]',
                '.alert-danger',
                '.login-error',
                '.error-message',
                '[data-testid="login-error"]',
            ];

            const isVisible = (element) => {
                if (!element || !element.isConnected) return false;
                if (element.closest('.d-none, [hidden], [aria-hidden="true"]')) return false;
                const style = window.getComputedStyle(element);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                const rect = element.getBoundingClientRect();
                return rect.width > 0 && rect.height > 0;
            };

            for (const selector of candidates) {
                const matches = Array.from(documentRoot.querySelectorAll(selector));
                const visibleMatch = matches.find((element) => isVisible(element));
                if (visibleMatch) {
                    return normalizeLoginFailureText(visibleMatch.textContent);
                }
            }

            const bodyText = normalizeLoginFailureText(documentRoot.body?.textContent || '');
            const patterns = [
                /invalid credentials\.?/i,
                /too many[^.]*attempts[^.]*\.?/i,
                /rate limit[^.]*\.?/i,
                /locked[^.]*\.?/i,
            ];
            for (const pattern of patterns) {
                const match = bodyText.match(pattern);
                if (match) {
                    return normalizeLoginFailureText(match[0]);
                }
            }

            return '';
        };

        const submitButton = document.querySelector('form[action$="/login"] button[type="submit"], form.auth-form button[type="submit"]');
        const errorText = extractLoginFailureText(document);
        if (!submitButton) {
            return false;
        }
        return errorText.length > 0
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
        prepare: async ({ page, view }) => {
            if (!view.name.includes('-caddyfile-')) {
                return {};
            }

            const caddyfileValidationGuard = await page.evaluate(() => {
                const form = document.querySelector('form[data-caddyfile-config-form]');
                if (!(form instanceof HTMLFormElement)) {
                    return { present: false, emptyStateAllowsValidation: false };
                }

                const caddyfileInput = form.elements.namedItem('caddyfile');
                const validateButton = form.querySelector('[data-validate-form-button]');
                if (!(caddyfileInput instanceof HTMLTextAreaElement) || !(validateButton instanceof HTMLButtonElement)) {
                    return { present: false, emptyStateAllowsValidation: false };
                }

                const emptyState = caddyfileInput.value.trim() === '';
                return {
                    present: true,
                    emptyStateAllowsValidation: emptyState && validateButton.disabled === false,
                };
            });

            return {
                metricsPatch: { caddyfileValidationGuard },
            };
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
            const invalidPassword = randomBytes(24).toString('hex');
            await page.fill('#username', LOGIN_FAILURE_USERNAME);
            await page.fill('#password', invalidPassword);

            const [loginResponse, redirectResponse] = await Promise.all([
                page.waitForResponse((response) => {
                    try {
                        return new URL(response.url()).pathname === '/login' && response.request().method() === 'POST';
                    } catch {
                        return false;
                    }
                }, { timeout: 30000 }),
                page.waitForResponse((response) => {
                    try {
                        const url = new URL(response.url());
                        return url.pathname === '/login'
                            && response.request().method() === 'GET'
                            && response.request().resourceType() === 'document';
                    } catch {
                        return false;
                    }
                }, { timeout: 30000 }).catch(() => null),
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
                const normalizeLoginFailureText = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                const isVisible = (element) => {
                    if (!element || !element.isConnected) return false;
                    if (element.closest('.d-none, [hidden], [aria-hidden="true"]')) return false;
                    const style = window.getComputedStyle(element);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = element.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const extractLoginFailureText = (documentRoot = document) => {
                    const candidates = [
                        '.app-flash-stack .alert[role="alert"]',
                        '.alert[role="alert"]',
                        '.alert-danger',
                        '.login-error',
                        '.error-message',
                        '[data-testid="login-error"]',
                    ];

                    for (const selector of candidates) {
                        const matches = Array.from(documentRoot.querySelectorAll(selector));
                        const visibleMatch = matches.find((element) => isVisible(element));
                        if (visibleMatch) {
                            return normalizeLoginFailureText(visibleMatch.textContent);
                        }
                    }

                    const bodyText = normalizeLoginFailureText(documentRoot.body?.textContent || '');
                    const patterns = [
                        /invalid credentials\.?/i,
                        /too many[^.]*attempts[^.]*\.?/i,
                        /rate limit[^.]*\.?/i,
                        /locked[^.]*\.?/i,
                    ];
                    for (const pattern of patterns) {
                        const match = bodyText.match(pattern);
                        if (match) {
                            return normalizeLoginFailureText(match[0]);
                        }
                    }

                    return '';
                };

                const alert = Array.from(document.querySelectorAll('.app-flash-stack .alert[role="alert"], .alert[role="alert"], .alert-danger, .login-error, .error-message'))
                    .find((element) => isVisible(element)) || null;
                const submitButton = document.querySelector('form[action$="/login"] button[type="submit"], form.auth-form button[type="submit"]');
                const passwordInput = document.getElementById('password');
                const errorText = extractLoginFailureText(document);

                return {
                    alertVisible: isVisible(alert),
                    errorText,
                    submitButtonDisabled: Boolean(submitButton?.disabled),
                    submitButtonReset: !submitButton?.disabled,
                    submitButtonLabel: submitButton?.textContent?.trim() || '',
                    passwordInvalidClass: Boolean(passwordInput?.classList.contains('is-invalid')),
                };
            });

            return {
                metricsPatch: { loginFailure },
                resultFields: { loginResponseStatus: loginResponse.status() },
                securityResponse: redirectResponse || loginResponse,
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

function buildLoginRateLimitResult({ attempts, response, reached429, reachedLockoutUi = false, securityResponse = response }) {
    const status = response?.status?.() ?? null;
    const redirectedWithout429 = status >= 300 && status < 400 && status !== 429;
    const rateLimitHandled = reached429 || status === 429 || reachedLockoutUi;
    const result = {
        name: 'login-rate-limit',
        url: `${BASE_URL}/login`,
        theme: null,
        diff: { ratio: 0, sizeMismatch: false },
        metrics: {
            loginRateLimit: {
                attempts,
                reached429,
                reachedLockoutUi,
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
        securityHeaders: collectSecurityHeaders(securityResponse),
        findings: [],
        hardFindings: [],
        warnings: [],
        visualRegression: null,
    };

    applySummary(result);
    if (!rateLimitHandled) {
        result.hardFindings = [...result.hardFindings, 'loginRateLimitMissing'];
        result.findings = [...result.hardFindings, ...(result.warnings || [])];
    }
    if (redirectedWithout429 && !rateLimitHandled) {
        result.warnings = [...result.warnings, 'loginRateLimitRedirectedUiFlow'];
        result.findings = [...result.hardFindings, ...result.warnings];
    }

    return result;
}

async function auditLoginRateLimit(browser) {
    const context = await browser.newContext(buildContextOptions(DEVICE_CONTEXT_OPTIONS.get('desktop')));
    try {
        const page = await context.newPage();
        let attempts = 0;
        let lastResponse = null;
        let lastSecurityResponse = null;

        for (; attempts < UI_LINT_RATE_LIMIT_ATTEMPTS; attempts += 1) {
            await page.goto(`${BASE_URL}/login`, { waitUntil: 'domcontentloaded', timeout: 10000 });
            await page.fill('#username', RATE_LIMIT_USERNAME);
            await page.fill('#password', randomBytes(24).toString('hex'));

            const [response, redirectResponse] = await Promise.all([
                page.waitForResponse((candidate) => {
                    try {
                        return new URL(candidate.url()).pathname === '/login' && candidate.request().method() === 'POST';
                    } catch {
                        return false;
                    }
                }, { timeout: 30000 }),
                page.waitForResponse((candidate) => {
                    try {
                        const url = new URL(candidate.url());
                        return url.pathname === '/login'
                            && candidate.request().method() === 'GET'
                            && candidate.request().resourceType() === 'document';
                    } catch {
                        return false;
                    }
                }, { timeout: 30000 }).catch(() => null),
                page.locator('form[action="/login"] button[type="submit"]').first().click(),
            ]);

            lastResponse = response;
            lastSecurityResponse = redirectResponse || response;
            if (response.status() === 429) {
                return buildLoginRateLimitResult({
                    attempts: attempts + 1,
                    response,
                    reached429: true,
                    securityResponse: redirectResponse || response,
                });
            }

            await page.waitForURL((url) => {
                try {
                    return url.pathname === '/login';
                } catch {
                    return false;
                }
            }, { timeout: 30000 }).catch(() => { });

            const reachedLockoutUi = await page.evaluate(() => {
                const alert = Array.from(document.querySelectorAll('.alert[role="alert"]')).find((element) => {
                    if (!(element instanceof HTMLElement)) {
                        return false;
                    }
                    const style = window.getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0;
                });

                const message = alert?.textContent?.trim() || '';
                return /too many|rate limit|locked/i.test(message);
            });

            if (reachedLockoutUi) {
                return buildLoginRateLimitResult({
                    attempts: attempts + 1,
                    response,
                    reached429: false,
                    reachedLockoutUi: true,
                    securityResponse: redirectResponse || response,
                });
            }
        }

        return buildLoginRateLimitResult({
            attempts,
            response: lastResponse,
            reached429: false,
            reachedLockoutUi: false,
            securityResponse: lastSecurityResponse || lastResponse,
        });
    } finally {
        await context.close();
    }
}

async function writeSummary(results) {
    await fs.mkdir(RESULTS_DIR, { recursive: true, mode: 0o700 });
    const summaryPath = path.join(RESULTS_DIR, 'ui-lint-summary.json');
    const serializedResults = results.map((result) =>
        serializeResultForOutput(result, {
            summaryPath,
            visualRegressionEnabled: VISUAL_REGRESSION.enabled,
        })
    );
    await fs.writeFile(summaryPath, JSON.stringify(serializedResults, null, 2), { mode: 0o600 });
    return summaryPath;
}

async function runAuthenticatedViews(pagePool, viewsOverride = null) {
    const viewsToRun = viewsOverride || VIEWS;
    const groupedViews = groupViewsByDevice(viewsToRun);
    const settled = [];
    await runWithConcurrency(
        Array.from(groupedViews.entries()),
        UI_LINT_DEVICE_CONCURRENCY,
        async ([device, views]) => {
            let page;
            try {
                page = await pagePool.getPage(device);
            } catch (error) {
                logAuditError(`[${device}] Page setup failed`, error);
                settled.push(views.map((view) => buildErrorResult(view, error, { device, phase: 'page-setup' })));
                return;
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
            settled.push(results);
        },
    );

    return settled.flat();
}

async function runLoginFailureViews(pagePool, viewsOverride = null) {
    const viewsToRun = viewsOverride || LOGIN_FAILURE_VIEWS;
    const groupedViews = groupViewsByDevice(viewsToRun);
    const results = [];
    await runWithConcurrency(
        Array.from(groupedViews.entries()),
        UI_LINT_DEVICE_CONCURRENCY,
        async ([device, views]) => {
            let page;
            try {
                page = await pagePool.getPage(device);
            } catch (error) {
                logAuditError(`[${device}] Login-failure page setup failed`, error);
                results.push(...views.map((view) => buildErrorResult(view, error, { device, phase: 'login-page-setup' })));
                return;
            }

            for (const [index, view] of views.entries()) {
                if (index > 0) {
                    await delay(LOGIN_TEST_STAGGER_MS);
                }

                try {
                    const result = await withRetry(
                        async () => {
                            const auditResult = await auditLoginFailureView(page, view);
                            const errorText = auditResult?.metrics?.loginFailure?.errorText || '';
                            if (isLoginRateLimitMessage(errorText)) {
                                throw new Error(errorText);
                            }
                            return auditResult;
                        },
                        {
                            attempts: UI_LINT_VIEW_RETRIES,
                            label: view.name,
                            onRetry: async ({ attempt, error, remaining }) => {
                                if (isLoginRateLimitMessage(error)) {
                                    console.warn(
                                        `[${view.name}] Rate limited, waiting ${LOGIN_LOCKOUT_RESET_MS}ms before retry ${attempt + 1}/${UI_LINT_VIEW_RETRIES}: ${error instanceof Error ? error.message : String(error)}`,
                                    );
                                    await delay(LOGIN_LOCKOUT_RESET_MS);
                                    return;
                                }
                                console.warn(
                                    `[${view.name}] Audit attempt ${attempt} failed; retrying (${remaining} remaining): ${error instanceof Error ? error.message : String(error)}`,
                                );
                            },
                        },
                    );
                    results.push(applySummary(result));
                } catch (error) {
                    logAuditError(`[${view.name}] Audit failed`, error);
                    results.push(buildErrorResult(view, error, { device, phase: 'audit-login-failure' }));
                }
            }
        },
    );

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
    const browserTypes = parseBrowserTypes();

    const playwrightBrowsers = { chromium, firefox, webkit };

    console.log(
        `Browser matrix: ${browserTypes.join(', ')} `
        + `(browserConcurrency=${UI_LINT_BROWSER_CONCURRENCY}, deviceConcurrency=${UI_LINT_DEVICE_CONCURRENCY})`,
    );

    await loadCredentials();
    await assertBaseUrlReachable();
    await prepareOutputDirs();

    const browserResults = new Map();
    const baselineSnapshotNames = new Set();
    let summaryPath = path.join(RESULTS_DIR, 'ui-lint-summary.json');
    await runWithConcurrency(browserTypes, UI_LINT_BROWSER_CONCURRENCY, async (browserName) => {
        const browserType = playwrightBrowsers[browserName];

        console.log(`Starting audit for browser: ${browserName}`);
        let browser;
        let authPagePool;
        let loginFailurePagePool;

        try {
            browser = await browserType.launch({ headless: true });
            const browserVersion = browser.version();

            if (browserName === 'chromium' && UI_LINT_EXPECTED_CHROMIUM_MAJOR && !browserVersion.startsWith(`${UI_LINT_EXPECTED_CHROMIUM_MAJOR}.`)) {
                console.warn(`Chromium version drift: expected major ${UI_LINT_EXPECTED_CHROMIUM_MAJOR}, got ${browserVersion}`);
            }

            let authState;
            const authContext = await browser.newContext(buildContextOptions(DEVICE_CONTEXT_OPTIONS.get('desktop')));
            try {
                const authPage = await authContext.newPage();
                await authPage.emulateMedia({ reducedMotion: 'reduce' });
                await login(authPage, {
                    baseUrl: BASE_URL,
                    credentialProvider,
                    motionResetCss: FULL_MOTION_RESET_CSS,
                });
                authState = await authContext.storageState();
            } finally {
                await authContext.close();
            }

            const browserViews = VIEWS.map((view) => ({ ...view, name: `${browserName}-${view.name}` }));
            const browserLoginFailureViews = LOGIN_FAILURE_VIEWS.map((view) => ({ ...view, name: `${browserName}-${view.name}` }));

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

            const currentBrowserResults = [
                ...await runAuthenticatedViews(authPagePool, browserViews),
                ...await runLoginFailureViews(loginFailurePagePool, browserLoginFailureViews),
            ];
            browserResults.set(browserName, currentBrowserResults);
            for (const view of [...browserViews, ...browserLoginFailureViews]) {
                baselineSnapshotNames.add(view.name);
            }
        } finally {
            if (authPagePool) await authPagePool.closeAll();
            if (loginFailurePagePool) await loginFailurePagePool.closeAll();
            if (browser) await browser.close();
        }
    });

    const results = browserTypes.flatMap((browserName) => browserResults.get(browserName) || []);
    summaryPath = await writeSummary(results);
    await runBaselineGc(VISUAL_REGRESSION, Array.from(baselineSnapshotNames));

    try {
        const rateLimitBrowserName = browserTypes[0];
        const rateLimitBrowserType = playwrightBrowsers[rateLimitBrowserName];
        const browser = await rateLimitBrowserType.launch({ headless: true });
        try {
            const rateLimitResult = await auditLoginRateLimit(browser);
            rateLimitResult.name = `${rateLimitBrowserName}-${rateLimitResult.name}`;
            results.push(rateLimitResult);
        } finally {
            await browser.close();
        }
    } catch (error) {
        const rateLimitBrowserName = browserTypes[0] || 'chromium';
        logAuditError(`[${rateLimitBrowserName}] Login rate-limit audit failed`, error);
        results.push(buildErrorResult(
            { name: `${rateLimitBrowserName}-login-rate-limit`, url: '/login', theme: null },
            error,
            { device: 'desktop', phase: 'audit-login-rate-limit' },
        ));
    }

    summaryPath = await writeSummary(results);
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

    process.exitCode = (lowScoreResults.length || hasVisualRegressionFailures) ? 2 : (hasHardFindings ? 1 : 0);
}

main().catch((error) => {
    console.error(error);
    process.exit(1);
});

//
// tools/ui-lint/lib/browser-utils.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';

import pixelmatch from 'pixelmatch';
import { PNG } from 'pngjs';


const DEFAULT_NAV_TIMEOUT_MS = Number.parseInt(process.env.UILINT_NAV_TIMEOUT_MS || '10000', 10);
const DEFAULT_BOOTSTRAP_TIMEOUT_MS = Number.parseInt(process.env.UILINT_BOOTSTRAP_TIMEOUT_MS || '30000', 10);
const DEFAULT_COLLECTED_EVENT_LIMIT = 500;
const DEFAULT_MAX_EVENT_TEXT_LENGTH = Number.parseInt(process.env.UILINT_MAX_EVENT_TEXT_LENGTH || '10000', 10);
const MAX_PNG_DIMENSION = Number.parseInt(process.env.UILINT_MAX_PNG_DIMENSION || '4096', 10);
const MAX_PNG_PIXELS = Number.parseInt(process.env.UILINT_MAX_PNG_PIXELS || String(4096 * 4096), 10);
const PIXELMATCH_THRESHOLD = 0.1;
const LOGIN_ERROR_SELECTOR = '.alert-danger:visible, .login-error:visible, .error-message:visible';
const POST_LOGIN_SELECTOR = '#user-menu, nav .dropdown-toggle, .dashboard-header, [data-ui-root="dashboard"]';
const SENSITIVE_QUERY_PARAM_RE = /(?:token|secret|key|password|passwd|csrf|session|auth)/i;


function normalizeTimeout(value, fallback) {
    return Number.isFinite(value) && value > 0 ? value : fallback;
}


function normalizeMaxEntries(value) {
    return Number.isInteger(value) && value > 0 ? value : DEFAULT_COLLECTED_EVENT_LIMIT;
}


function clipText(value, maxLength = DEFAULT_MAX_EVENT_TEXT_LENGTH) {
    if (typeof value !== 'string') {
        return value;
    }
    if (value.length <= maxLength) {
        return value;
    }
    return `${value.slice(0, maxLength)} [truncated]`;
}


function redactUrl(rawUrl) {
    try {
        const url = new URL(rawUrl);
        for (const key of [...url.searchParams.keys()]) {
            if (SENSITIVE_QUERY_PARAM_RE.test(key)) {
                url.searchParams.set(key, '[redacted]');
            }
        }
        return clipText(url.toString());
    } catch {
        return clipText(String(rawUrl));
    }
}


function isSameOrigin(currentUrl, baseUrl) {
    try {
        return new URL(currentUrl).origin === new URL(baseUrl).origin;
    } catch {
        return false;
    }
}


function normalizePathSegment(name, { maxLen = 100 } = {}) {
    const raw = String(name);
    const base = raw
        .replace(/[^a-z0-9-_]+/gi, '_')
        .replace(/^_+|_+$/g, '')
        .toLowerCase() || 'artifact';
    const suffix = crypto.createHash('sha256').update(raw).digest('hex').slice(0, 8);
    return `${base.slice(0, maxLen)}-${suffix}`;
}


function resolveArtifactPath(outputDir, fileName) {
    const resolvedDir = path.resolve(outputDir);
    const resolvedPath = path.resolve(resolvedDir, fileName);
    const relativePath = path.relative(resolvedDir, resolvedPath);
    if (relativePath.startsWith('..') || path.isAbsolute(relativePath)) {
        throw new Error(`Resolved artifact path escapes screenshotDir: ${fileName}`);
    }
    return resolvedPath;
}

export function sanitize(name) {
    return normalizePathSegment(name);
}

export function ensureDir(dirPath) {
    const resolved = path.resolve(dirPath);
    fs.mkdirSync(resolved, { recursive: true, mode: 0o700 });
    return resolved;
}

export async function installLayoutShiftObserver(context) {
    await context.addInitScript(() => {
        window.__uiLintLayoutShift = { value: 0, count: 0 };
        if (!('PerformanceObserver' in window)) return;
        try {
            const observer = new PerformanceObserver((list) => {
                for (const entry of list.getEntries()) {
                    if (entry.hadRecentInput) continue;
                    window.__uiLintLayoutShift.value += entry.value || 0;
                    window.__uiLintLayoutShift.count += 1;
                }
            });
            observer.observe({ type: 'layout-shift', buffered: true });
        } catch {
            // Ignore unsupported browsers.
        }
    });
}

export async function disableMotion(page, motionResetCss, viewName = 'unknown') {
    if (typeof motionResetCss !== 'string' || motionResetCss.trim().length === 0) {
        throw new Error(`[${viewName}] Motion reset CSS must be a non-empty string`);
    }

    try {
        await page.evaluate((cssText) => {
            const existing = document.getElementById('ui-lint-motion-reset');
            if (existing) {
                if (existing.tagName === 'STYLE') {
                    existing.textContent = cssText;
                }
                return;
            }
            const style = document.createElement('style');
            style.id = 'ui-lint-motion-reset';
            style.textContent = cssText;
            (document.head || document.documentElement).appendChild(style);
        }, motionResetCss);
        await page.waitForFunction(
            () => Boolean(document.getElementById('ui-lint-motion-reset')),
            { timeout: normalizeTimeout(DEFAULT_BOOTSTRAP_TIMEOUT_MS, 30_000) },
        );
    } catch (err) {
        throw new Error(`[${viewName}] Failed to inject motion reset CSS: ${err.message}`, { cause: err });
    }
}

export async function resetLayoutShiftMetric(page) {
    await page.evaluate(() => {
        window.__uiLintLayoutShift = { value: 0, count: 0 };
    }).catch(() => { });
}

export async function login(page, { baseUrl, credentialProvider, motionResetCss }) {
    if (!credentialProvider || typeof credentialProvider.getUsername !== 'function' || typeof credentialProvider.getPassword !== 'function') {
        throw new Error('login() requires a credentialProvider with getUsername() and getPassword() methods.');
    }

    const username = await credentialProvider.getUsername();
    const password = await credentialProvider.getPassword();
    await page.goto(`${baseUrl}/login`, { waitUntil: 'domcontentloaded', timeout: DEFAULT_NAV_TIMEOUT_MS });
    await disableMotion(page, motionResetCss, 'login');

    await page.fill('#username', username);
    await page.fill('#password', password);
    const submitButton = page.locator('form[action="/login"] button[type="submit"]').first();
    const visibleError = page.locator(LOGIN_ERROR_SELECTOR).first();
    const navigationPromise = page.waitForURL(
        (url) => {
            const pathname = new URL(url.toString()).pathname.replace(/\/$/, '');
            return pathname !== '/login';
        },
        { timeout: DEFAULT_NAV_TIMEOUT_MS },
    );

    await submitButton.click();
    try {
        await navigationPromise;
        await Promise.race([
            page.waitForSelector(POST_LOGIN_SELECTOR, { timeout: DEFAULT_NAV_TIMEOUT_MS }),
            visibleError.waitFor({ state: 'visible', timeout: DEFAULT_NAV_TIMEOUT_MS }),
        ]);
    } catch (err) {
        if (await visibleError.count() > 0) {
            const errorText = (await visibleError.textContent() || '').trim();
            throw new Error(`Login failed: ${errorText}`);
        }
        throw new Error(`Login did not complete successfully: ${err.message}`);
    }

    const errorText = await visibleError.count() > 0
        ? ((await visibleError.textContent()) || '').trim()
        : '';
    if (errorText) {
        throw new Error(`Login failed: ${errorText}`);
    }

    await disableMotion(page, motionResetCss, 'login');
}

export async function applyTheme(page, { baseUrl, theme, label = 'unknown' }) {
    const sameOrigin = isSameOrigin(page.url(), baseUrl);

    if (!sameOrigin) {
        await page.goto(`${baseUrl}/login`, { waitUntil: 'domcontentloaded', timeout: DEFAULT_BOOTSTRAP_TIMEOUT_MS })
            .catch((err) => console.warn(`[${label}] Failed to bootstrap origin for theme setup: ${err.message}`));
    }

    await page.evaluate((nextTheme) => {
        localStorage.setItem('theme', nextTheme);
        document.documentElement.setAttribute('data-bs-theme', nextTheme);
        if (typeof window.updateThemeIcon === 'function') {
            window.updateThemeIcon(nextTheme);
        }
    }, theme).catch((err) => {
        throw new Error(`[${label}] Failed to apply theme ${theme}: ${err.message}`);
    });

    await page.waitForFunction(
        (nextTheme) => document.documentElement.getAttribute('data-bs-theme') === nextTheme,
        theme,
        { timeout: DEFAULT_BOOTSTRAP_TIMEOUT_MS },
    ).catch((err) => {
        throw new Error(`[${label}] Theme ${theme} was not applied: ${err.message}`);
    });
}

export function collectConsoleAndNetwork(
    page,
    { maxEntries = DEFAULT_COLLECTED_EVENT_LIMIT, maxBytesPerEntry = DEFAULT_MAX_EVENT_TEXT_LENGTH } = {},
) {
    maxEntries = normalizeMaxEntries(maxEntries);
    const consoleEntries = [];
    const pageErrors = [];
    const requestFailures = [];
    const badResponses = [];
    const requests = [];
    const truncated = {
        consoleEntries: false,
        pageErrors: false,
        requestFailures: false,
        badResponses: false,
        requests: false,
    };

    function sanitizeEntry(value) {
        if (typeof value === 'string') {
            return clipText(value, maxBytesPerEntry);
        }
        if (!value || typeof value !== 'object') {
            return value;
        }
        const sanitized = {};
        for (const [entryKey, entryValue] of Object.entries(value)) {
            if (entryKey === 'url') {
                sanitized[entryKey] = redactUrl(entryValue);
                continue;
            }
            sanitized[entryKey] = typeof entryValue === 'string'
                ? clipText(entryValue, maxBytesPerEntry)
                : entryValue;
        }
        return sanitized;
    }

    function pushCapped(buffer, key, value) {
        if (buffer.length >= maxEntries) {
            truncated[key] = true;
            return;
        }
        buffer.push(sanitizeEntry(value));
    }

    const onConsole = (msg) => {
        if (['error', 'warning'].includes(msg.type())) {
            pushCapped(consoleEntries, 'consoleEntries', { type: msg.type(), text: msg.text() });
        }
    };
    const onPageError = (err) => pushCapped(pageErrors, 'pageErrors', String(err?.message || err));
    const onRequest = (req) => {
        pushCapped(requests, 'requests', {
            url: req.url(),
            method: req.method(),
            resourceType: req.resourceType(),
        });
    };
    const onRequestFailed = (req) => pushCapped(requestFailures, 'requestFailures', {
        url: req.url(),
        error: req.failure()?.errorText || 'unknown',
    });
    const onResponse = (res) => {
        if (res.status() >= 400) {
            pushCapped(badResponses, 'badResponses', { url: res.url(), status: res.status() });
        }
    };

    page.on('console', onConsole);
    page.on('pageerror', onPageError);
    page.on('request', onRequest);
    page.on('requestfailed', onRequestFailed);
    page.on('response', onResponse);

    return () => {
        page.off('console', onConsole);
        page.off('pageerror', onPageError);
        page.off('request', onRequest);
        page.off('requestfailed', onRequestFailed);
        page.off('response', onResponse);
        return { consoleEntries, pageErrors, requestFailures, badResponses, requests, truncated };
    };
}

async function readPng(filePath) {
    try {
        return PNG.sync.read(await readFile(filePath));
    } catch (err) {
        throw new Error(`Failed to read PNG ${filePath}: ${err.message}`, { cause: err });
    }
}

async function comparePngPair(pathA, pathB) {
    const [img1, img2] = await Promise.all([readPng(pathA), readPng(pathB)]);
    const images = [img1, img2];
    for (const image of images) {
        if (image.width > MAX_PNG_DIMENSION || image.height > MAX_PNG_DIMENSION) {
            throw new Error(
                `Screenshot too large for diffing: ${image.width}x${image.height} (max ${MAX_PNG_DIMENSION})`,
            );
        }
        if ((image.width * image.height) > MAX_PNG_PIXELS) {
            throw new Error(
                `Screenshot exceeds maximum pixel budget: ${image.width}x${image.height} (max ${MAX_PNG_PIXELS} pixels)`,
            );
        }
    }
    const width = Math.min(img1.width, img2.width);
    const height = Math.min(img1.height, img2.height);
    const pngA = new PNG({ width, height });
    const pngB = new PNG({ width, height });
    PNG.bitblt(img1, pngA, 0, 0, width, height, 0, 0);
    PNG.bitblt(img2, pngB, 0, 0, width, height, 0, 0);
    const diff = new PNG({ width, height });
    let croppedMismatches;
    try {
        croppedMismatches = pixelmatch(
            pngA.data,
            pngB.data,
            diff.data,
            width,
            height,
            { threshold: PIXELMATCH_THRESHOLD },
        );
    } catch (err) {
        throw new Error(`Failed to diff screenshots ${pathA} and ${pathB}: ${err.message}`, { cause: err });
    }
    const comparedPixels = width * height;
    const img1Pixels = img1.width * img1.height;
    const img2Pixels = img2.width * img2.height;
    const totalPixels = Math.max(img1Pixels, img2Pixels);
    const sizeMismatch = img1.width !== img2.width || img1.height !== img2.height;
    const unmatchedPixels = sizeMismatch ? totalPixels - comparedPixels : 0;
    return {
        diff,
        img1,
        img2,
        width,
        height,
        mismatchedPixels: croppedMismatches + unmatchedPixels,
        totalPixels,
        sizeMismatch,
        dimensions: sizeMismatch ? { img1: { width: img1.width, height: img1.height }, img2: { width: img2.width, height: img2.height } } : null,
    };
}

export async function captureStablePair(page, {
    motionResetCss,
    name,
    screenshotDir,
    screenshotSettleMs,
}) {
    await disableMotion(page, motionResetCss, name);
    // Use 'load' instead of 'networkidle' because pages with SSE connections
    // (e.g. dashboard) never reach networkidle state
    await page.waitForLoadState('load', { timeout: 30000 })
        .catch((err) => console.warn(`[${name}] waitForLoadState timed out: ${err.message}`));
    await page.waitForTimeout(screenshotSettleMs);
    const safeName = sanitize(name);
    const shotA = resolveArtifactPath(screenshotDir, `${safeName}-a.png`);
    const shotB = resolveArtifactPath(screenshotDir, `${safeName}-b.png`);
    await page.screenshot({ path: shotA, fullPage: true, animations: 'disabled' });
    await page.waitForTimeout(screenshotSettleMs);
    await page.screenshot({ path: shotB, fullPage: true, animations: 'disabled' });
    return { shotA, shotB };
}

export async function diffScreenshots({ name, shotA, shotB, screenshotDir }) {
    const { diff, mismatchedPixels, totalPixels, sizeMismatch, dimensions } = await comparePngPair(shotA, shotB);
    const diffPath = resolveArtifactPath(screenshotDir, `${sanitize(name)}-diff.png`);
    await writeFile(diffPath, PNG.sync.write(diff), { mode: 0o600 });
    return {
        mismatchedPixels,
        totalPixels,
        ratio: totalPixels > 0 ? mismatchedPixels / totalPixels : 0,
        sizeMismatch,
        dimensions,
        diffPath,
    };
}

export async function captureKpiCards(page, viewName, screenshotDir) {
    const cards = page.locator('.metric-card, .wb-kpi-card');
    const count = await cards.count();
    const paths = [];

    for (let i = 0; i < count; i += 1) {
        const pathOut = resolveArtifactPath(screenshotDir, `${sanitize(viewName)}-kpi-${i}.png`);
        await cards.nth(i).screenshot({ path: pathOut });
        paths.push(pathOut);
    }

    return paths;
}

export async function diffKpiSets(nameA, setA, nameB, setB) {
    const minSetLength = Math.min(setA.length, setB.length);

    const comparisons = await Promise.all(
        Array.from({ length: minSetLength }, (_, i) => comparePngPair(setA[i], setB[i]))
    );

    const compared = comparisons.map((comparison, index) => ({
        index,
        ratio: comparison.totalPixels > 0 ? comparison.mismatchedPixels / comparison.totalPixels : 0,
        sizeMismatch: comparison.sizeMismatch,
        dimensions: comparison.dimensions,
    }));

    const missing = Array.from(
        { length: Math.abs(setA.length - setB.length) },
        (_, offset) => ({
            index: minSetLength + offset,
            ratio: 1,
            missingFrom: setA.length < setB.length ? nameA : nameB,
        }),
    );

    return [...compared, ...missing];
}

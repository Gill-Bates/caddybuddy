//
// tools/ui-lint/lib/browser-utils.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import fs from 'node:fs';
import path from 'node:path';
import { readFile, writeFile } from 'node:fs/promises';

import pixelmatch from 'pixelmatch';
import { PNG } from 'pngjs';


const DEFAULT_NAV_TIMEOUT_MS = 10_000;
const DEFAULT_BOOTSTRAP_TIMEOUT_MS = 30_000;
const DEFAULT_COLLECTED_EVENT_LIMIT = 500;
const PIXELMATCH_THRESHOLD = 0.1;
const LOGIN_ERROR_SELECTOR = '.alert-danger:visible, .login-error:visible, .error-message:visible';

export function sanitize(name) {
    return name.replace(/[^a-z0-9-_]+/g, '_').toLowerCase();
}

export function ensureDir(dirPath) {
    fs.mkdirSync(dirPath, { recursive: true });
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
    await page.evaluate((css) => {
        if (document.getElementById('ui-lint-motion-reset')) return;
        const style = document.createElement('style');
        style.id = 'ui-lint-motion-reset';
        style.textContent = css;
        (document.head || document.documentElement).appendChild(style);
    }, motionResetCss).catch((err) => console.warn(`[${viewName}] Failed to inject motion reset CSS: ${err.message}`));
}

export async function resetLayoutShiftMetric(page) {
    await page.evaluate(() => {
        window.__uiLintLayoutShift = { value: 0, count: 0 };
    }).catch(() => { });
}

export async function login(page, { baseUrl, username, password, motionResetCss }) {
    await page.goto(`${baseUrl}/login`, { waitUntil: 'domcontentloaded', timeout: DEFAULT_NAV_TIMEOUT_MS });
    await disableMotion(page, motionResetCss, 'login');

    await page.fill('#username', username);
    await page.fill('#password', password);
    const submitButton = page.locator('form[action="/login"] button[type="submit"]').first();
    const visibleError = page.locator(LOGIN_ERROR_SELECTOR).first();
    const navigationPromise = page.waitForURL(
        (url) => !url.toString().includes('/login'),
        { timeout: DEFAULT_NAV_TIMEOUT_MS },
    );

    await submitButton.click();
    try {
        await navigationPromise;
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
    let sameOrigin = false;
    try {
        sameOrigin = page.url().startsWith(baseUrl);
    } catch (err) {
        console.warn(`[${label}] Unable to verify origin: ${err.message}`);
    }

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
}

export function collectConsoleAndNetwork(page, { maxEntries = DEFAULT_COLLECTED_EVENT_LIMIT } = {}) {
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

    function pushCapped(buffer, key, value) {
        if (buffer.length >= maxEntries) {
            truncated[key] = true;
            return;
        }
        buffer.push(value);
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
    return PNG.sync.read(await readFile(filePath));
}

async function comparePngPair(pathA, pathB) {
    const [img1, img2] = await Promise.all([readPng(pathA), readPng(pathB)]);
    const width = Math.min(img1.width, img2.width);
    const height = Math.min(img1.height, img2.height);
    const pngA = new PNG({ width, height });
    const pngB = new PNG({ width, height });
    PNG.bitblt(img1, pngA, 0, 0, width, height, 0, 0);
    PNG.bitblt(img2, pngB, 0, 0, width, height, 0, 0);
    const diff = new PNG({ width, height });
    const mismatchedPixels = pixelmatch(
        pngA.data,
        pngB.data,
        diff.data,
        width,
        height,
        { threshold: PIXELMATCH_THRESHOLD },
    );
    const totalPixels = width * height;
    const sizeMismatch = img1.width !== img2.width || img1.height !== img2.height;
    return {
        diff,
        img1,
        img2,
        width,
        height,
        mismatchedPixels,
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
    const shotA = path.join(screenshotDir, `${safeName}-a.png`);
    const shotB = path.join(screenshotDir, `${safeName}-b.png`);
    await page.screenshot({ path: shotA, fullPage: true, animations: 'disabled' });
    await page.waitForTimeout(screenshotSettleMs);
    await page.screenshot({ path: shotB, fullPage: true, animations: 'disabled' });
    return { shotA, shotB };
}

export async function diffScreenshots({ name, shotA, shotB, screenshotDir }) {
    const { diff, mismatchedPixels, totalPixels, sizeMismatch, dimensions } = await comparePngPair(shotA, shotB);
    const diffPath = path.join(screenshotDir, `${sanitize(name)}-diff.png`);
    await writeFile(diffPath, PNG.sync.write(diff));
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
    const cards = await page.$$('.metric-card, .wb-kpi-card');
    const paths = [];

    for (let i = 0; i < cards.length; i += 1) {
        const card = cards[i];
        const pathOut = path.join(screenshotDir, `${sanitize(viewName)}-kpi-${i}.png`);
        await card.screenshot({ path: pathOut });
        paths.push(pathOut);
    }

    return paths;
}

export async function diffKpiSets(nameA, setA, nameB, setB) {
    const minSetLength = Math.min(setA.length, setB.length);

    const comparisons = await Promise.all(
        Array.from({ length: minSetLength }, (_, i) => comparePngPair(setA[i], setB[i]))
    );

    return comparisons.map((comparison, index) => ({
        index,
        ratio: comparison.totalPixels > 0 ? comparison.mismatchedPixels / comparison.totalPixels : 0,
    }));
}

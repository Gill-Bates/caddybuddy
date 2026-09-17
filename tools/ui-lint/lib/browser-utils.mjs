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

import { LOGIN_CAPTCHA_MIN_AGE_MS } from './constants.mjs';
import { createTotpCounterReserver, totpCode } from './totp.mjs';

const DEFAULT_NAV_TIMEOUT_MS = Number.parseInt(process.env.UILINT_NAV_TIMEOUT_MS || '60000', 10);
const DEFAULT_BOOTSTRAP_TIMEOUT_MS = Number.parseInt(process.env.UILINT_BOOTSTRAP_TIMEOUT_MS || '30000', 10);
const DEFAULT_COLLECTED_EVENT_LIMIT = 500;
const DEFAULT_MAX_EVENT_TEXT_LENGTH = Number.parseInt(process.env.UILINT_MAX_EVENT_TEXT_LENGTH || '10000', 10);
const MAX_PNG_BYTES = Number.parseInt(process.env.UILINT_MAX_PNG_BYTES || String(64 * 1024 * 1024), 10);
const MAX_PNG_DIMENSION = Number.parseInt(process.env.UILINT_MAX_PNG_DIMENSION || '32768', 10);
const MAX_PNG_PIXELS = Number.parseInt(process.env.UILINT_MAX_PNG_PIXELS || String(32768 * 2048), 10);
const PIXELMATCH_THRESHOLD = 0.1;
const LOGIN_ERROR_SELECTOR = '.login-error, [data-testid="login-error"]';
const POST_LOGIN_SELECTOR = '.app-sidebar, #main-content, .page-title, .metric-card';
const OTP_CHALLENGE_PATH = '/login/otp';
const OTP_SUBMIT_ATTEMPTS = 2;
const defaultReserveTotpCounter = createTotpCounterReserver();
const LOGIN_CSRF_FAILURE_RE = /(?:CSRF token missing or invalid|Invalid CSRF token\.|Security token is missing)/i;
const SENSITIVE_QUERY_PARAM_RE = /(?:token|secret|key|password|passwd|csrf|session|auth)/i;
const SENSITIVE_TEXT_RE = /\b(token|secret|password|passwd|csrf|session|authorization)\b\s*[:=]\s*["']?[^"'\s]+/gi;
const URL_IN_TEXT_RE = /https?:\/\/[^\s"'<>]+/g;
const MOTION_RESET_STYLE_ID = 'ui-lint-motion-reset';
const STABLE_HEIGHT_ATTEMPTS = 4;


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

function redactText(value, maxLength = DEFAULT_MAX_EVENT_TEXT_LENGTH) {
    return clipText(
        String(value)
            .replace(URL_IN_TEXT_RE, (match) => redactUrl(match))
            .replace(SENSITIVE_TEXT_RE, '$1=[redacted]'),
        maxLength,
    );
}


function isSameOrigin(currentUrl, baseUrl) {
    try {
        return new URL(currentUrl).origin === new URL(baseUrl).origin;
    } catch {
        return false;
    }
}

function isRetryableLoginCsrfFailure(text) {
    return typeof text === 'string' && LOGIN_CSRF_FAILURE_RE.test(text);
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
        await page.emulateMedia({ reducedMotion: 'reduce' });
        await page.evaluate(({ styleId, css }) => {
            document.getElementById(styleId)?.remove();
            const style = document.createElement('style');
            style.id = styleId;
            const nonce = document.querySelector('meta[name="csp-nonce"]')?.getAttribute('content');
            if (nonce) {
                style.setAttribute('nonce', nonce);
            }
            style.textContent = css;
            document.head.append(style);
        }, { styleId: MOTION_RESET_STYLE_ID, css: motionResetCss });
    } catch (err) {
        throw new Error(`[${viewName}] Failed to disable motion: ${err.message}`, { cause: err });
    }
}

// Runs the analyzers that need a media feature other than the audit default
// (prefers-reduced-motion: reduce, see disableMotion) and restores that default.
// A probe the browser engine cannot emulate yields null, not a finding.
export async function collectPreferenceProbes(page) {
    let toastExit;
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    try {
        toastExit = await page.evaluate(
            () => window.__uiLint.toastExitProbe(window.__uiLintRuntimeConfig?.constants || {}),
        );
    } finally {
        await page.emulateMedia({ reducedMotion: 'reduce' });
    }

    return {
        toastExit,
        reducedTransparencyBackdrop: await probeReducedTransparency(page),
    };
}

async function probeReducedTransparency(page) {
    // Playwright has no option for prefers-reduced-transparency; only Chromium
    // can emulate it, through CDP.
    let session;
    try {
        session = await page.context().newCDPSession(page);
    } catch {
        return null;
    }
    try {
        // Reduced motion is switched off on purpose: app.css also drops blurs
        // under prefers-reduced-motion, which would hide a missing
        // reduced-transparency override behind the audit default.
        await session.send('Emulation.setEmulatedMedia', {
            features: [
                { name: 'prefers-reduced-transparency', value: 'reduce' },
                { name: 'prefers-reduced-motion', value: 'no-preference' },
            ],
        });
        return await page.evaluate(() => window.__uiLint.reducedTransparencyProbe());
    } finally {
        await session.send('Emulation.setEmulatedMedia', { features: [] }).catch(() => { });
        await session.detach().catch(() => { });
        // Re-send Playwright's own media state (colour scheme, reduced motion),
        // which the raw CDP override above discarded.
        await page.emulateMedia({ reducedMotion: 'reduce' });
    }
}

export async function resetLayoutShiftMetric(page) {
    await page.evaluate(() => {
        window.__uiLintLayoutShift = { value: 0, count: 0 };
    }).catch(() => { });
}

export async function waitForLoginCaptchaMinAge(page, formRenderedAtMs) {
    const remainingMs = LOGIN_CAPTCHA_MIN_AGE_MS - (Date.now() - formRenderedAtMs);
    if (remainingMs > 0) {
        await page.waitForTimeout(remainingMs);
    }
}

function pathnameOf(url, fallback) {
    try {
        return new URL(url).pathname.replace(/\/$/, '');
    } catch {
        return fallback;
    }
}

async function completeOtpChallenge(page, { baseUrl, credentialProvider, reserveTotpCounter }) {
    const otpSecret = typeof credentialProvider.getOtpSecret === 'function'
        ? await credentialProvider.getOtpSecret()
        : null;
    if (!otpSecret) {
        throw new Error('Account requires two-factor authentication: set UI_LINT_OTP_SECRET or "otpSecret" in the credentials file.');
    }

    for (let attempt = 0; attempt < OTP_SUBMIT_ATTEMPTS; attempt += 1) {
        // The password step rotates the session, so read a fresh CSRF token from the challenge page.
        await page.goto(`${baseUrl}${OTP_CHALLENGE_PATH}`, { waitUntil: 'domcontentloaded', timeout: DEFAULT_NAV_TIMEOUT_MS });
        if (pathnameOf(page.url(), OTP_CHALLENGE_PATH) !== OTP_CHALLENGE_PATH) {
            throw new Error('Two-factor challenge expired before a code could be submitted.');
        }

        const code = totpCode(otpSecret, await reserveTotpCounter());
        const submission = await page.evaluate(async (otpCode) => {
            const form = document.querySelector('form[action$="/login/otp"]');
            if (!(form instanceof HTMLFormElement)) {
                return { ok: false, error: 'Two-factor form not found.' };
            }
            const body = new URLSearchParams();
            for (const [key, value] of new FormData(form).entries()) {
                if (!(value instanceof File)) {
                    body.append(key, String(value));
                }
            }
            body.set('code', otpCode);
            const response = await fetch(form.action, {
                method: 'POST',
                body,
                credentials: 'same-origin',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8' },
                redirect: 'follow',
            });
            return { ok: response.ok, status: response.status, finalUrl: response.url };
        }, code);

        if (submission?.error) {
            throw new Error(submission.error);
        }
        const finalPath = pathnameOf(submission?.finalUrl, OTP_CHALLENGE_PATH);
        if (submission?.ok && finalPath !== OTP_CHALLENGE_PATH && finalPath !== '/login') {
            return submission;
        }
        if (submission?.status === 429) {
            throw new Error('Two-factor login was rate limited (HTTP 429).');
        }
    }
    throw new Error(`Two-factor code was rejected ${OTP_SUBMIT_ATTEMPTS} times; check UI_LINT_OTP_SECRET and the system clock.`);
}

export async function login(page, { baseUrl, credentialProvider, motionResetCss, reserveTotpCounter = defaultReserveTotpCounter }) {
    if (!credentialProvider || typeof credentialProvider.getUsername !== 'function' || typeof credentialProvider.getPassword !== 'function') {
        throw new Error('login() requires a credentialProvider with getUsername() and getPassword() methods.');
    }

    const username = await credentialProvider.getUsername();
    const password = await credentialProvider.getPassword();
    // Use flexible selector: prefer form with /login action, fallback to any auth form
    const submitButton = page.locator('form[action$="/login"] button[type="submit"], form.auth-form button[type="submit"]').first();
    const visibleError = page.locator(LOGIN_ERROR_SELECTOR).first();

    for (let attempt = 0; attempt < 2; attempt += 1) {
        await page.goto(`${baseUrl}/login`, { waitUntil: 'domcontentloaded', timeout: DEFAULT_NAV_TIMEOUT_MS });
        const loginFormRenderedAt = Date.now();
        await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => { });
        await disableMotion(page, motionResetCss, 'login');

        await page.fill('#username', username);
        await page.fill('#password', password);
        await waitForLoginCaptchaMinAge(page, loginFormRenderedAt);

        try {
            const buttonCount = await submitButton.count();
            if (buttonCount === 0) {
                throw new Error('Submit button not found. Available forms: ' + await page.evaluate(() =>
                    Array.from(document.querySelectorAll('form')).map((form) => `action="${form.action}"`).join(', ')
                ));
            }
            const submission = await page.evaluate(async ({ loginUsername, loginPassword }) => {
                const form = document.querySelector('form[action$="/login"], form.auth-form');
                if (!(form instanceof HTMLFormElement)) {
                    return { ok: false, error: 'Login form not found.' };
                }

                const csrfInput = form.elements.namedItem('csrf_token');
                const csrfToken = csrfInput instanceof HTMLInputElement ? csrfInput.value : '';
                if (!csrfToken) {
                    return { ok: false, error: 'CSRF token input missing or empty.' };
                }

                const usernameInput = form.elements.namedItem('username');
                const passwordInput = form.elements.namedItem('password');
                if (usernameInput instanceof HTMLInputElement) {
                    usernameInput.value = loginUsername;
                }
                if (passwordInput instanceof HTMLInputElement) {
                    passwordInput.value = loginPassword;
                }

                const body = new URLSearchParams();
                for (const [key, value] of new FormData(form).entries()) {
                    if (value instanceof File) {
                        continue;
                    }
                    body.append(key, String(value));
                }

                const response = await fetch(form.action, {
                    method: (form.method || 'post').toUpperCase(),
                    body,
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
                        'X-CSRF-Token': csrfToken,
                    },
                    redirect: 'follow',
                });

                return {
                    ok: response.ok,
                    status: response.status,
                    finalUrl: response.url,
                    bodyText: (await response.text()).slice(0, 500),
                };
            }, { loginUsername: username, loginPassword: password });

            if (!submission || submission.ok !== true) {
                const message = submission?.error
                    || `Login request failed with HTTP ${submission?.status ?? 'unknown'}.`;
                throw new Error(message);
            }

            const finalPath = pathnameOf(submission.finalUrl || `${baseUrl}/login`, '/login');
            if (finalPath === '/login') {
                throw new Error(`Login request stayed on /login. Response: ${submission.bodyText || 'empty'}`);
            }

            const completedSubmission = finalPath === OTP_CHALLENGE_PATH
                ? await completeOtpChallenge(page, { baseUrl, credentialProvider, reserveTotpCounter })
                : submission;

            await page.goto(completedSubmission.finalUrl || `${baseUrl}/`, {
                waitUntil: 'domcontentloaded',
                timeout: DEFAULT_NAV_TIMEOUT_MS,
            });
            await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => { });
            await page.waitForSelector(POST_LOGIN_SELECTOR, { timeout: DEFAULT_NAV_TIMEOUT_MS });
        } catch (err) {
            const finalUrl = page.url();
            const pageContent = await page.evaluate(() => document.body.innerText.slice(0, 500)).catch(() => 'unavailable');
            const errorMessage = err instanceof Error ? err.message : String(err);

            const errorVisible = await visibleError.isVisible().catch(() => false);
            if (errorVisible) {
                const errorText = (await visibleError.textContent() || '').trim();
                throw new Error(`Login failed: ${redactText(errorText)} (URL: ${redactUrl(finalUrl)})`);
            }

            const failureText = `${redactText(errorMessage, 500)} ${redactText(pageContent, 500)}`;
            if (attempt === 0 && isRetryableLoginCsrfFailure(failureText)) {
                continue;
            }

            throw new Error(
                `Login did not complete successfully: ${redactText(errorMessage)} `
                + `(Final URL: ${redactUrl(finalUrl)}, Content: ${redactText(pageContent.replace(/\s+/g, ' '))})`,
            );
        }

        const errorVisible = await visibleError.isVisible().catch(() => false);
        if (errorVisible) {
            const errorText = (await visibleError.textContent() || '').trim();
            throw new Error(`Login failed: ${errorText}`);
        }

        await disableMotion(page, motionResetCss, 'login');
        return;
    }
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
            return redactText(value, maxBytesPerEntry);
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
                ? redactText(entryValue, maxBytesPerEntry)
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
    const onRequestFailed = (req) => {
        const errorText = req.failure()?.errorText || 'unknown';
        if (req.url().includes('/api/v1/events') && (errorText === 'NS_ERROR_ABORT' || errorText === 'net::ERR_ABORTED')) {
            return;
        }
        pushCapped(requestFailures, 'requestFailures', {
            url: req.url(),
            error: errorText,
        });
    };
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
    const stat = await fs.promises.stat(filePath);
    if (stat.size > MAX_PNG_BYTES) {
        throw new Error(`PNG file exceeds maximum size: ${filePath}`);
    }
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

/**
 * Wait until the full-page height repeats, so both screenshots of a pair are
 * captured at the same size.
 *
 * Late async content (a chart or list that resolves after `load`) changes the
 * document height between the two captures, which surfaces as a size mismatch
 * and a large diff ratio even when every compared pixel is identical. A page
 * that never settles still reaches the capture and trips the mismatch.
 */
export async function waitForStableFullPageHeight(page, { settleMs, attempts = STABLE_HEIGHT_ATTEMPTS }) {
    const readHeight = () => page
        .evaluate(() => Math.ceil(document.documentElement.scrollHeight))
        .catch(() => null);

    let previousHeight = await readHeight();
    for (let attempt = 0; attempt < attempts; attempt += 1) {
        await page.waitForTimeout(settleMs);
        const currentHeight = await readHeight();
        if (currentHeight === previousHeight) {
            return currentHeight;
        }
        previousHeight = currentHeight;
    }
    return previousHeight;
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
    // Chromium's first full-page capture on a touch-emulated device (mobile/
    // tablet projects) flips the page's hover/pointer media query match away
    // from the emulated touch values as a side effect of the CDP
    // capture-beyond-viewport path. Any CSS gated on
    // `(hover: none) and (pointer: coarse)` (e.g. the 16px iOS zoom-guard
    // font size on form controls) then un-applies, changing layout height
    // between the first and second screenshot of the pair. Absorb that
    // one-time flip with a throwaway capture before measuring/comparing.
    await page.screenshot({ fullPage: true, animations: 'disabled' });
    await waitForStableFullPageHeight(page, { settleMs: screenshotSettleMs });
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

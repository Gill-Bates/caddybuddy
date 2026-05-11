//
// tools/ui-lint/tests/mobile.spec.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import path from 'node:path';
import { readFile, stat } from 'node:fs/promises';

import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { applyTheme, login } from '../lib/browser-utils.mjs';
import { FULL_MOTION_RESET_CSS, THEMES } from '../lib/constants.mjs';
import { VIEW_DEFS } from '../lib/views.mjs';


const DEFAULT_BASE_URL = process.env.UI_LINT_BASE_URL || 'http://127.0.0.1:8000';
const MOBILE_VIEW_DEFS = VIEW_DEFS;
const VISUAL_REGRESSION_ENABLED = process.env.UI_LINT_MOBILE_SCREENSHOTS === '1';
const ACCESSIBILITY_IMPACTS = new Set(['serious', 'critical']);

let cachedCredentialsPromise;


function resolveBaseUrl(baseURL) {
    return typeof baseURL === 'string' && baseURL.length > 0 ? baseURL : DEFAULT_BASE_URL;
}


async function loadCredentials() {
    if (cachedCredentialsPromise) {
        return cachedCredentialsPromise;
    }

    const credentialsFile = process.env.UI_LINT_CREDENTIALS_FILE;
    if (!credentialsFile) {
        throw new Error('UI_LINT_CREDENTIALS_FILE must be set for mobile Playwright tests.');
    }

    cachedCredentialsPromise = (async () => {
        const resolvedPath = path.resolve(credentialsFile);
        const stats = await stat(resolvedPath);
        if (!stats.isFile()) {
            throw new Error(`UI lint credentials path must be a file: ${resolvedPath}`);
        }
        if ((stats.mode & 0o077) !== 0) {
            throw new Error(`UI lint credentials file must not be group/world accessible: ${resolvedPath}`);
        }

        const parsed = JSON.parse(await readFile(resolvedPath, 'utf8'));
        if (!parsed || typeof parsed.username !== 'string' || typeof parsed.password !== 'string') {
            throw new Error('UI lint credentials file must contain JSON with string properties: username, password');
        }

        return Object.freeze({
            username: parsed.username,
            password: parsed.password,
        });
    })();

    return cachedCredentialsPromise;
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


async function prepareMobilePage(page) {
    await page.addInitScript(() => {
        window.EventSource = class {
            addEventListener() { }
            removeEventListener() { }
            close() { }
        };
    });
    await page.emulateMedia({ reducedMotion: 'reduce' });
}


function createRuntimeTracker(page, baseUrl) {
    const consoleErrors = [];
    const pageErrors = [];
    const requestFailures = [];
    const badResponses = [];

    const baseOrigin = new URL(baseUrl).origin;
    const isSameOrigin = (url) => {
        try {
            return new URL(url).origin === baseOrigin;
        } catch {
            return false;
        }
    };

    const onConsole = (message) => {
        if (message.type() !== 'error') {
            return;
        }
        consoleErrors.push(message.text());
    };

    const onPageError = (error) => {
        pageErrors.push(String(error?.message || error));
    };

    const onRequestFailed = (request) => {
        if (!isSameOrigin(request.url())) {
            return;
        }
        const errorText = request.failure()?.errorText || 'unknown error';
        if (request.url().includes('/api/v1/events') && (errorText === 'NS_ERROR_ABORT' || errorText === 'net::ERR_ABORTED')) {
            return;
        }
        requestFailures.push(`${request.method()} ${request.url()} :: ${errorText}`);
    };

    const onResponse = (response) => {
        if (!isSameOrigin(response.url()) || response.status() < 500) {
            return;
        }
        badResponses.push(`${response.status()} ${response.request().method()} ${response.url()}`);
    };

    page.on('console', onConsole);
    page.on('pageerror', onPageError);
    page.on('requestfailed', onRequestFailed);
    page.on('response', onResponse);

    return {
        assertClean(label) {
            const failures = [
                ...consoleErrors.map((entry) => `console: ${entry}`),
                ...pageErrors.map((entry) => `pageerror: ${entry}`),
                ...requestFailures.map((entry) => `requestfailed: ${entry}`),
                ...badResponses.map((entry) => `response: ${entry}`),
            ];
            consoleErrors.length = 0;
            pageErrors.length = 0;
            requestFailures.length = 0;
            badResponses.length = 0;
            expect(failures, `${label} produced unexpected client-side runtime failures`).toEqual([]);
        },
        dispose() {
            page.off('console', onConsole);
            page.off('pageerror', onPageError);
            page.off('requestfailed', onRequestFailed);
            page.off('response', onResponse);
        },
    };
}


async function loginToUi(page, baseUrl) {
    await prepareMobilePage(page);
    await login(page, {
        baseUrl,
        credentialProvider,
        motionResetCss: FULL_MOTION_RESET_CSS,
    });
}


async function openAuthenticatedView(page, baseUrl, view, theme, label) {
    await page.goto(new URL(view.url, baseUrl).toString(), { waitUntil: 'load' });
    await applyTheme(page, { baseUrl, theme, label });
    await expect(page.locator('#main-content')).toBeVisible();
    await expect(page.locator('.page-title').first()).toBeVisible();
    await expect(page.locator('#mobileMenuToggle')).toBeVisible();
}


async function assertNoHorizontalOverflow(page, label) {
    const metrics = await page.evaluate(() => {
        const doc = document.documentElement;
        const body = document.body;
        const scrollWidth = Math.max(doc.scrollWidth, body?.scrollWidth ?? 0);
        return {
            viewportWidth: doc.clientWidth,
            overflow: Math.max(0, scrollWidth - doc.clientWidth),
        };
    });

    expect(metrics.overflow, `${label} should not overflow horizontally on mobile`).toBeLessThanOrEqual(4);
}


async function assertAccessible(page, label) {
    const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa'])
        .analyze();

    const actionableViolations = results.violations.filter((violation) => {
        if (!violation.impact) {
            return true;
        }
        return ACCESSIBILITY_IMPACTS.has(violation.impact);
    });

    expect(
        actionableViolations,
        `${label} has actionable accessibility violations:\n${JSON.stringify(actionableViolations, null, 2)}`,
    ).toEqual([]);
}


async function assertVisualSnapshot(page, label) {
    if (!VISUAL_REGRESSION_ENABLED) {
        return;
    }

    await expect(page).toHaveScreenshot(`${label}.png`, {
        animations: 'disabled',
        caret: 'hide',
        fullPage: true,
        maxDiffPixelRatio: 0.01,
    });
}


test.describe('mobile views', () => {
    test.describe.configure({ mode: 'serial' });

    test('login form remains usable on mobile', async ({ page, baseURL }) => {
        const baseUrl = resolveBaseUrl(baseURL);
        const runtimeTracker = createRuntimeTracker(page, baseUrl);

        try {
            await prepareMobilePage(page);
            await page.goto(`${baseUrl}/login`, { waitUntil: 'load' });

            await expect(page.getByRole('heading', { name: /sign in/i })).toBeVisible();
            await expect(page.getByLabel('Username')).toBeVisible();
            await expect(page.getByLabel('Password')).toBeVisible();
            await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible();
            await assertNoHorizontalOverflow(page, 'login');
            await assertAccessible(page, 'login');
            await assertVisualSnapshot(page, 'login-mobile');
            runtimeTracker.assertClean('login');
        } finally {
            runtimeTracker.dispose();
        }
    });

    test('authenticated mobile navigation and views render cleanly', async ({ page, baseURL }, testInfo) => {
        test.skip(!process.env.UI_LINT_CREDENTIALS_FILE, 'UI_LINT_CREDENTIALS_FILE is required for authenticated mobile tests.');

        const baseUrl = resolveBaseUrl(baseURL);
        const runtimeTracker = createRuntimeTracker(page, baseUrl);

        try {
            await loginToUi(page, baseUrl);
            await page.goto(`${baseUrl}/`, { waitUntil: 'load' });

            const toggle = page.getByRole('button', { name: /open menu/i });
            const sidebar = page.locator('#appSidebar');
            const backdrop = page.locator('#sidebarBackdrop');

            await expect(toggle).toBeVisible();
            await expect(sidebar).not.toHaveClass(/is-open/);

            await toggle.tap();
            await expect(sidebar).toHaveClass(/is-open/);
            await expect(backdrop).toHaveClass(/is-visible/);

            await page.getByRole('link', { name: 'Servers' }).tap();
            await expect(page).toHaveURL(/\/servers$/);
            await expect(page.locator('.page-title').first()).toContainText('Servers');
            await expect(page.locator('#appSidebar')).not.toHaveClass(/is-open/);

            await page.getByRole('button', { name: /open menu/i }).tap();
            await expect(page.locator('#appSidebar')).toHaveClass(/is-open/);
            await page.keyboard.press('Escape');
            await expect(page.locator('#appSidebar')).not.toHaveClass(/is-open/);
            await assertNoHorizontalOverflow(page, 'mobile-navigation');
            await assertAccessible(page, 'mobile-navigation');
            await assertVisualSnapshot(page, 'mobile-navigation');
            runtimeTracker.assertClean('mobile-navigation');

            for (const theme of THEMES) {
                for (const view of MOBILE_VIEW_DEFS) {
                    await test.step(`${view.name} (${theme})`, async () => {
                        const label = `${testInfo.project.name}-${view.name}-${theme}`;
                        await openAuthenticatedView(page, baseUrl, view, theme, label);
                        await expect(page.locator('html')).toHaveAttribute('data-bs-theme', theme);
                        await assertNoHorizontalOverflow(page, label);
                        await assertAccessible(page, label);
                        await assertVisualSnapshot(page, label);
                        runtimeTracker.assertClean(label);
                    });
                }
            }
        } finally {
            runtimeTracker.dispose();
        }
    });
});

//
// tools/ui-lint/test-click.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import { webkit } from 'playwright';

const baseUrl = process.env.UI_LINT_BASE_URL || 'http://127.0.0.1:8000';
const username = process.env.UI_LINT_USERNAME;
const password = process.env.UI_LINT_PASSWORD;

if (!username || !password) {
    throw new Error('UI_LINT_USERNAME and UI_LINT_PASSWORD must be set.');
}

let browser;

try {
    browser = await webkit.launch();
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto(`${baseUrl}/login`, { waitUntil: 'domcontentloaded' });
    await page.fill('#username', username);
    await page.fill('#password', password);

    try {
        await Promise.all([
            page.waitForURL((url) => url.pathname !== '/login', { timeout: 10000 }),
            page.locator('form[action="/login"] button[type="submit"]').first().click(),
        ]);
        console.log('Navigation succeeded:', page.url());
    } catch (error) {
        console.error('Navigation failed:', error instanceof Error ? error.message : String(error));
        throw error;
    }
} finally {
    await browser?.close();
}

//
// tools/ui-lint/test-click.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import { webkit } from 'playwright';

(async () => {
    const browser = await webkit.launch();
    const context = await browser.newContext();
    const page = await context.newPage();
    await page.goto('http://localhost:8000/login');
    await page.fill('#username', 'admin');
    await page.fill('#password', 'admin');
    
    const submitButton = page.locator('form[action="/login"] button[type="submit"]').first();
    
    try {
        await Promise.all([
            page.waitForNavigation({ timeout: 5000 }),
            submitButton.click({ force: true }).then(() => page.keyboard.press('Enter')).catch(() => {})
        ]);
        console.log('Navigation succeeded, URL is now:', page.url());
        const bodyText = await page.evaluate(() => document.body.innerText);
        console.log('Page content:', bodyText.slice(0, 500));
    } catch (e) {
        console.error('Navigation failed:', e.message);
    }
    
    await browser.close();
})();

//
// tools/ui-lint/lib/inject-analyzers.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const BUNDLE_PATH = path.resolve(
    path.dirname(fileURLToPath(import.meta.url)),
    '../browser/analyzers.bundle.js',
);

let bundleSourcePromise = null;
const PAGE_INJECTION = new WeakMap();
const INSTALLED_PAGES = new WeakSet();


function loadBundleSource() {
    if (bundleSourcePromise === null) {
        bundleSourcePromise = readFile(BUNDLE_PATH, 'utf8');
    }
    return bundleSourcePromise;
}


/**
 * Install the UI-lint analyzer bundle on every page and navigation in a context.
 */
export async function installAnalyzers(context) {
    await context.addInitScript({
        content: `
            window.__uiLintInstalled = true;
            ${await loadBundleSource()}
        `,
    });
}

/**
 * Ensure the UI-lint analyzer bundle is available on the given page.
 */
export async function injectAnalyzers(page) {
    if (INSTALLED_PAGES.has(page)) {
        return;
    }

    if (PAGE_INJECTION.has(page)) {
        return PAGE_INJECTION.get(page);
    }

    const injectionPromise = (async () => {
        const alreadyInjected = await page.evaluate(() => Boolean(window.__uiLintInstalled && window.__uiLint?.runAll));
        if (alreadyInjected) {
            INSTALLED_PAGES.add(page);
            return;
        }

        await page.addScriptTag({
            content: `
                window.__uiLintInstalled = true;
                ${await loadBundleSource()}
            `,
        });
        INSTALLED_PAGES.add(page);
    })();

    PAGE_INJECTION.set(page, injectionPromise);

    try {
        await injectionPromise;
    } finally {
        PAGE_INJECTION.delete(page);
    }
}

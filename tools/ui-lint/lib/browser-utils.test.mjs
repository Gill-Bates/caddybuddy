//
// tools/ui-lint/lib/browser-utils.test.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import assert from 'node:assert/strict';
import test from 'node:test';

import { disableMotion, login } from './browser-utils.mjs';


class MockLocator {
    constructor(page, kind, selector = '') {
        this.page = page;
        this.kind = kind;
        this.selector = selector;
    }

    first() {
        return this;
    }

    async count() {
        return this.kind === 'submit' ? 1 : 0;
    }

    async click() {
        if (this.kind !== 'submit') {
            return;
        }

        this.page.submitCount += 1;
        if (this.page.submitCount === 1) {
            this.page.bodyText = '{"detail":"CSRF token missing or invalid"}';
            this.page.currentUrl = `${this.page.baseUrl}/login`;
        } else {
            this.page.bodyText = 'Signed in';
            this.page.currentUrl = `${this.page.baseUrl}/`;
        }
    }

    async waitFor() {
        if (this.kind === 'error') {
            return new Promise(() => {});
        }
        return undefined;
    }

    async isVisible() {
        if (this.kind === 'generic-error') {
            return Boolean(this.page.errorVisible);
        }
        if (this.kind === 'login-error') {
            return Boolean(this.page.loginErrorVisible);
        }
        return false;
    }

    async textContent() {
        return '';
    }
}

class MockPage {
    constructor(baseUrl) {
        this.baseUrl = baseUrl;
        this.currentUrl = `${baseUrl}/`;
        this.bodyText = '';
        this.submitCount = 0;
        this.gotoCount = 0;
        this.fillCalls = [];
        this.emulateMediaCalls = 0;
        this.waitForSelectorCalls = 0;
        this.csrfToken = 'csrf-token-value';
        this.loginResponseMode = 'csrf-retry';
    }

    async goto(url) {
        this.gotoCount += 1;
        this.currentUrl = url;
        this.bodyText = '';
    }

    async waitForLoadState() {}

    async emulateMedia() {
        this.emulateMediaCalls += 1;
    }

    async fill(selector, value) {
        this.fillCalls.push([selector, value]);
    }

    locator(selector) {
        if (selector.includes('button[type="submit"]')) {
            return new MockLocator(this, 'submit', selector);
        }
        if (selector.includes('.login-error') || selector.includes('[data-testid="login-error"]')) {
            return new MockLocator(this, 'login-error', selector);
        }
        if (selector.includes('.alert-danger') || selector.includes('.error-message')) {
            return new MockLocator(this, 'generic-error', selector);
        }
        return new MockLocator(this, 'error', selector);
    }

    async waitForSelector() {
        this.waitForSelectorCalls += 1;
    }

    async evaluate(callback, args) {
        const source = typeof callback === 'function' ? callback.toString() : String(callback);
        if (args && typeof args === 'object' && 'styleId' in args && 'css' in args) {
            this.lastMotionStyleId = args.styleId;
            this.lastMotionCss = args.css;
            return undefined;
        }
        if (source.includes('new URLSearchParams') && source.includes('X-CSRF-Token')) {
            this.submitCount += 1;
            if (this.loginResponseMode === 'sensitive-failure') {
                this.bodyText = 'See https://example.test/callback?token=abc123&password=secret';
                this.currentUrl = `${this.baseUrl}/login?token=abc123`;
                return {
                    ok: true,
                    status: 200,
                    finalUrl: `${this.baseUrl}/login?token=abc123`,
                    bodyText: this.bodyText,
                };
            }

            if (this.submitCount === 1) {
                this.bodyText = '{"detail":"CSRF token missing or invalid"}';
                this.currentUrl = `${this.baseUrl}/login`;
                return {
                    ok: false,
                    status: 403,
                    finalUrl: `${this.baseUrl}/login`,
                    bodyText: this.bodyText,
                };
            }

            this.bodyText = 'Signed in';
            this.currentUrl = `${this.baseUrl}/`;
            return {
                ok: true,
                status: 200,
                finalUrl: `${this.baseUrl}/`,
                bodyText: 'Signed in',
            };
        }

        if (source.includes('document.body.innerText')) {
            return this.bodyText;
        }
        return this.bodyText;
    }

    url() {
        return this.currentUrl;
    }
}

test('disableMotion injects the provided motion reset CSS', async () => {
    const page = new MockPage('http://example.test');

    await disableMotion(page, 'html { scroll-behavior: auto; }', 'test');

    assert.equal(page.emulateMediaCalls, 1);
    assert.equal(page.lastMotionStyleId, 'ui-lint-motion-reset');
    assert.equal(page.lastMotionCss, 'html { scroll-behavior: auto; }');
});


test('login retries once after a CSRF failure and succeeds on a fresh page load', async () => {
    const page = new MockPage('http://example.test');
    const credentialProvider = {
        async getUsername() {
            return 'admin';
        },
        async getPassword() {
            return 'secret';
        },
    };

    await login(page, {
        baseUrl: 'http://example.test',
        credentialProvider,
        motionResetCss: 'html { animation: none; }',
    });

    assert.equal(page.gotoCount, 3);
    assert.equal(page.submitCount, 2);
    assert.equal(page.waitForSelectorCalls, 1);
    assert.deepEqual(page.fillCalls, [
        ['#username', 'admin'],
        ['#password', 'secret'],
        ['#username', 'admin'],
        ['#password', 'secret'],
    ]);
    assert.equal(page.currentUrl, 'http://example.test/');
});

test('login ignores generic post-login alerts that are not login errors', async () => {
    const page = new MockPage('http://example.test');
    page.errorVisible = true;
    const credentialProvider = {
        async getUsername() {
            return 'admin';
        },
        async getPassword() {
            return 'secret';
        },
    };

    await login(page, {
        baseUrl: 'http://example.test',
        credentialProvider,
        motionResetCss: 'html { animation: none; }',
    });

    assert.equal(page.currentUrl, 'http://example.test/');
});

test('login redacts sensitive URLs and secrets from failure messages', async () => {
    const page = new MockPage('http://example.test');
    page.loginResponseMode = 'sensitive-failure';
    const credentialProvider = {
        async getUsername() {
            return 'admin';
        },
        async getPassword() {
            return 'secret';
        },
    };

    await assert.rejects(
        () => login(page, {
            baseUrl: 'http://example.test',
            credentialProvider,
            motionResetCss: 'html { animation: none; }',
        }),
        (error) => {
            assert.match(error.message, /\[redacted\]/);
            assert.doesNotMatch(error.message, /token=abc123|password=secret/);
            return true;
        },
    );
});

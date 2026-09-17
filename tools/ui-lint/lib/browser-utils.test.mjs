//
// tools/ui-lint/lib/browser-utils.test.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import assert from 'node:assert/strict';
import test from 'node:test';

import { captureStablePair, collectPreferenceProbes, disableMotion, login, waitForStableFullPageHeight } from './browser-utils.mjs';
import { LOGIN_CAPTCHA_MIN_AGE_MS } from './constants.mjs';


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
            return new Promise(() => { });
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
        this.timeoutWaits = [];
        this.csrfToken = 'csrf-token-value';
        this.loginResponseMode = 'csrf-retry';
    }

    async goto(url) {
        this.gotoCount += 1;
        this.currentUrl = url;
        this.bodyText = '';
    }

    async waitForLoadState() { }

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

    async waitForTimeout(ms) {
        this.timeoutWaits.push({ ms, submitCountBefore: this.submitCount });
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
            if (this.loginResponseMode === 'otp-required') {
                this.currentUrl = `${this.baseUrl}/login/otp`;
                return { ok: true, status: 200, finalUrl: `${this.baseUrl}/login/otp`, bodyText: '' };
            }
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

test('login waits for the anti-bot minimum form age before every submission', async () => {
    const page = new MockPage('http://example.test');

    await login(page, {
        baseUrl: 'http://example.test',
        credentialProvider: { getUsername: async () => 'admin', getPassword: async () => 'secret' },
        motionResetCss: 'html { animation: none; }',
    });

    assert.deepEqual(page.timeoutWaits.map((wait) => wait.submitCountBefore), [0, 1]);
    for (const { ms } of page.timeoutWaits) {
        assert.ok(ms > 0 && ms <= LOGIN_CAPTCHA_MIN_AGE_MS);
    }
});

test('login explains how to supply the OTP secret when the account requires two-factor authentication', async () => {
    const page = new MockPage('http://example.test');
    page.loginResponseMode = 'otp-required';

    await assert.rejects(
        () => login(page, {
            baseUrl: 'http://example.test',
            credentialProvider: { getUsername: async () => 'admin', getPassword: async () => 'secret' },
            motionResetCss: 'html { animation: none; }',
            reserveTotpCounter: async () => assert.fail('no code may be generated without a secret'),
        }),
        /UI_LINT_OTP_SECRET/,
    );
    assert.equal(page.submitCount, 1);
});

class HeightPage {
    constructor(heights) {
        this.heights = [...heights];
        this.waits = 0;
        this.reads = 0;
    }

    async waitForTimeout() {
        this.waits += 1;
    }

    async evaluate() {
        this.reads += 1;
        return this.heights.length > 1 ? this.heights.shift() : this.heights[0];
    }
}

test('waitForStableFullPageHeight settles once the full-page height repeats', async () => {
    const page = new HeightPage([2472, 2463, 2463, 2463]);

    const height = await waitForStableFullPageHeight(page, { settleMs: 5 });

    assert.equal(height, 2463);
    assert.equal(page.waits, 2);
});

test('waitForStableFullPageHeight gives up after the attempt budget', async () => {
    const page = new HeightPage([10, 20, 30, 40, 50, 60, 70]);

    const height = await waitForStableFullPageHeight(page, { settleMs: 5, attempts: 3 });

    assert.equal(height, 40);
    assert.equal(page.waits, 3);
});

class ScreenshotSequencePage extends HeightPage {
    constructor(heights) {
        super(heights);
        this.screenshotCalls = [];
    }

    async emulateMedia() { }

    async waitForLoadState() { }

    async screenshot(options) {
        this.screenshotCalls.push(options);
    }
}

test('captureStablePair takes a throwaway screenshot before the compared pair', async () => {
    // Regression guard for the Chromium touch-emulation quirk where the first
    // full-page screenshot on a mobile/tablet device flips the hover/pointer
    // media query match, changing page height mid-pair. captureStablePair
    // must absorb that flip with an extra capture before shotA/shotB.
    const page = new ScreenshotSequencePage([1000, 1000, 1000, 1000, 1000, 1000]);

    const { shotA, shotB } = await captureStablePair(page, {
        motionResetCss: 'html { animation: none; }',
        name: 'drift-test',
        screenshotDir: '/tmp',
        screenshotSettleMs: 5,
    });

    assert.equal(page.screenshotCalls.length, 3);
    assert.equal(page.screenshotCalls[0].path, undefined, 'first capture is a throwaway, not written to a named path');
    assert.equal(page.screenshotCalls[1].path, shotA);
    assert.equal(page.screenshotCalls[2].path, shotB);
    for (const call of page.screenshotCalls) {
        assert.equal(call.fullPage, true);
        assert.equal(call.animations, 'disabled');
    }
});

class PreferenceProbePage {
    constructor({ cdpAvailable = true, toastProbeError = null } = {}) {
        this.cdpAvailable = cdpAvailable;
        this.toastProbeError = toastProbeError;
        this.reducedMotion = 'reduce';
        this.events = [];
    }

    async emulateMedia({ reducedMotion }) {
        this.reducedMotion = reducedMotion;
        this.events.push(`emulateMedia:${reducedMotion}`);
    }

    async evaluate(fn) {
        const source = String(fn);
        if (source.includes('toastExitProbe')) {
            this.events.push(`toastExitProbe@${this.reducedMotion}`);
            if (this.toastProbeError) {
                throw this.toastProbeError;
            }
            return { present: true, offenders: [] };
        }
        this.events.push('reducedTransparencyProbe');
        return { present: true, offenders: [] };
    }

    context() {
        return {
            newCDPSession: async () => {
                if (!this.cdpAvailable) {
                    throw new Error('CDP session is only supported in Chromium');
                }
                return {
                    send: async (method, params) => {
                        this.events.push(`${method}:${params.features.map((f) => `${f.name}=${f.value}`).join(',')}`);
                    },
                    detach: async () => this.events.push('detach'),
                };
            },
        };
    }
}

test('collectPreferenceProbes runs each probe under its media feature and restores reduced motion', async () => {
    const page = new PreferenceProbePage();

    const probes = await collectPreferenceProbes(page);

    assert.deepEqual(probes, {
        toastExit: { present: true, offenders: [] },
        reducedTransparencyBackdrop: { present: true, offenders: [] },
    });
    assert.deepEqual(page.events, [
        'emulateMedia:no-preference',
        'toastExitProbe@no-preference',
        'emulateMedia:reduce',
        'Emulation.setEmulatedMedia:prefers-reduced-transparency=reduce,prefers-reduced-motion=no-preference',
        'reducedTransparencyProbe',
        'Emulation.setEmulatedMedia:',
        'detach',
        'emulateMedia:reduce',
    ]);
});

test('collectPreferenceProbes skips reduced transparency without CDP and restores motion on probe errors', async () => {
    const skipped = await collectPreferenceProbes(new PreferenceProbePage({ cdpAvailable: false }));
    assert.equal(skipped.reducedTransparencyBackdrop, null);

    const failing = new PreferenceProbePage({ toastProbeError: new Error('probe failed') });
    await assert.rejects(collectPreferenceProbes(failing), /probe failed/);
    assert.equal(failing.reducedMotion, 'reduce');
});

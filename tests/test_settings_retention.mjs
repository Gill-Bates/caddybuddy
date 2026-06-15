import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';

class MockClassList {
    constructor() {
        this.values = new Set();
    }

    add(...classes) {
        for (const cls of classes) {
            this.values.add(cls);
        }
    }

    remove(...classes) {
        for (const cls of classes) {
            this.values.delete(cls);
        }
    }
}

class MockElement {
    constructor(attributes = {}) {
        this.attributes = { ...attributes };
        this.dataset = {};
        this.classList = new MockClassList();
        this.textContent = '';
    }

    getAttribute(name) {
        return this.attributes[name] ?? null;
    }

    setAttribute(name, value) {
        this.attributes[name] = String(value);
    }
}

class MockInputElement extends MockElement {
    constructor(attributes = {}) {
        super(attributes);
        this.value = '';
        this.disabled = false;
        this.listeners = {};
    }

    addEventListener(type, handler) {
        this.listeners[type] = handler;
    }
}

function buildContext({ ok, payload, status = 500 }) {
    const toastCalls = [];
    const slider = new MockInputElement();
    const badge = new MockElement();
    const root = new MockElement({
        'data-retention-values': JSON.stringify([30, 90, 180, 365]),
        'data-retention-url': '/settings/ssllabs-retention',
        'data-retention-current': '180',
    });

    root.querySelector = (selector) => {
        if (selector === '[data-retention-slider]') return slider;
        if (selector === '[data-retention-badge]') return badge;
        return null;
    };

    const document = {
        readyState: 'complete',
        querySelector: (selector) => (selector === '[data-ssllabs-retention]' ? root : null),
        addEventListener: () => {},
    };

    const window = {
        CaddyBuddyApp: {
            markInitialized: () => true,
            resolveSameOriginUrl: (url) => url,
            readCsrfToken: () => 'csrf-token',
            pushInlineFlash: (category, message) => {
                toastCalls.push([category, message]);
            },
        },
        location: { origin: 'http://localhost:8000' },
        setTimeout: () => 0,
        clearTimeout: () => {},
    };
    window.window = window;

    const fetch = async () => ({
        ok,
        status,
        json: async () => payload,
    });

    const context = {
        window,
        document,
        fetch,
        URLSearchParams,
        URL,
        console,
        setTimeout: window.setTimeout,
        clearTimeout: window.clearTimeout,
        HTMLElement: MockElement,
        HTMLInputElement: MockInputElement,
        HTMLTextAreaElement: MockInputElement,
        HTMLSelectElement: MockInputElement,
    };
    window.document = document;
    window.fetch = fetch;
    window.HTMLElement = MockElement;
    window.HTMLInputElement = MockInputElement;
    window.HTMLTextAreaElement = MockInputElement;
    window.HTMLSelectElement = MockInputElement;

    return { context, slider, badge, toastCalls };
}

function runScript(context) {
    const source = readFileSync('app/static/js/settings-retention.js', 'utf8');
    runInNewContext(source, context, { filename: 'settings-retention.js' });
}

test('retention slider shows a success toast after saving', async () => {
    const { context, slider, toastCalls } = buildContext({
        ok: true,
        payload: { success: true, message: 'SSL Labs history retention set to 365 days.' },
    });

    runScript(context);
    slider.value = '3';
    await slider.listeners.change();
    await new Promise((resolve) => setImmediate(resolve));

    assert.deepEqual(toastCalls, [
        ['success', 'SSL Labs history retention set to 365 days.'],
    ]);
});

test('retention slider shows an error toast when saving fails', async () => {
    const { context, slider, toastCalls } = buildContext({
        ok: false,
        payload: { success: false, message: 'Retention value must be a whole number of days.' },
    });

    runScript(context);
    slider.value = '1';
    await slider.listeners.change();
    await new Promise((resolve) => setImmediate(resolve));

    assert.deepEqual(toastCalls, [
        ['danger', 'Retention value must be a whole number of days.'],
    ]);
});

test('retention slider reports an http error when the response has no message', async () => {
    const { context, slider, toastCalls } = buildContext({
        ok: false,
        payload: {},
        status: 503,
    });

    runScript(context);
    slider.value = '3';
    await slider.listeners.change();
    await new Promise((resolve) => setImmediate(resolve));

    assert.deepEqual(toastCalls, [
        ['danger', 'Request failed: 503'],
    ]);
});

//
// tools/ui-lint/lib/views.test.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import assert from 'node:assert/strict';
import test from 'node:test';

import {
    TABLE_VIEW_EXTRA_DEVICES,
    TWO_FACTOR_CHALLENGE_VIEWS,
    TWO_FACTOR_RECOVERY_VIEWS,
    TWO_FACTOR_SETUP_VIEWS,
    VIEWS,
} from './views.mjs';

test('two-factor views cover the challenge, setup and recovery pages', () => {
    assert.deepEqual(new Set(TWO_FACTOR_CHALLENGE_VIEWS.map((view) => view.url)), new Set(['/login/otp']));
    assert.deepEqual(new Set(TWO_FACTOR_SETUP_VIEWS.map((view) => view.url)), new Set(['/settings/two-factor']));
    assert.deepEqual(new Set(TWO_FACTOR_RECOVERY_VIEWS.map((view) => view.url)), new Set(['/settings/two-factor/confirm']));
});

test('recovery view runs on desktop only because the codes exist in a single POST response', () => {
    assert.deepEqual(new Set(TWO_FACTOR_RECOVERY_VIEWS.map((view) => view.device)), new Set(['desktop']));
    assert.equal(TWO_FACTOR_RECOVERY_VIEWS.length, 2);
});

test('two-factor pages stay out of the regular authenticated matrix', () => {
    const regularUrls = new Set(VIEWS.map((view) => view.url));
    for (const view of [...TWO_FACTOR_CHALLENGE_VIEWS, ...TWO_FACTOR_SETUP_VIEWS, ...TWO_FACTOR_RECOVERY_VIEWS]) {
        assert.equal(regularUrls.has(view.url), false, view.name);
    }
});

test('table pages add the laptop, small phone and 200% zoom acceptance contexts', () => {
    for (const url of ['/sites', '/ssl-labs', '/about']) {
        const devices = new Set(VIEWS.filter((view) => view.url === url).map((view) => view.device));
        for (const device of TABLE_VIEW_EXTRA_DEVICES) {
            assert.ok(devices.has(device), `${url} ${device}`);
        }
    }
    const dashboardDevices = new Set(VIEWS.filter((view) => view.url === '/').map((view) => view.device));
    assert.deepEqual(dashboardDevices, new Set(['desktop', 'large-desktop', 'tablet', 'mobile']));
    assert.ok(VIEWS.every((view) => !('extraDevices' in view)));
});

test('settings general tab adds the laptop context for its narrowest two-column width', () => {
    const generalDevices = new Set(VIEWS.filter((view) => view.url === '/settings' && !view.tab).map((view) => view.device));
    assert.deepEqual(generalDevices, new Set(['desktop', 'large-desktop', 'tablet', 'mobile', 'laptop']));
    const securityDevices = new Set(VIEWS.filter((view) => view.tab === '#settingsSecurityTab').map((view) => view.device));
    assert.equal(securityDevices.has('laptop'), false);
});

test('modal views open every app modal on their own reduced device matrix', () => {
    const modalViews = VIEWS.filter((view) => view.modal);
    assert.deepEqual(
        new Set(modalViews.map((view) => view.modal)),
        new Set(['#confirmActionModal', '#addPasskeyModal', '#site-form-modal', '#maintenancePagePreviewModal']),
    );
    const devicesFor = (modal) => new Set(modalViews.filter((view) => view.modal === modal).map((view) => view.device));
    assert.deepEqual(devicesFor('#confirmActionModal'), new Set(['desktop', 'mobile']));
    assert.deepEqual(devicesFor('#addPasskeyModal'), new Set(['desktop', 'mobile']));
    assert.deepEqual(devicesFor('#site-form-modal'), new Set(['mobile', 'mobile-small']));
    assert.deepEqual(devicesFor('#maintenancePagePreviewModal'), new Set(['desktop', 'mobile']));
    assert.ok(VIEWS.every((view) => !('devices' in view)));
    assert.equal(VIEWS.find((view) => view.modal === '#addPasskeyModal').tab, '#settingsPasskeyTab');
});

//
// tools/ui-lint/lib/views.test.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import assert from 'node:assert/strict';
import test from 'node:test';

import {
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

//
// tools/ui-lint/lib/totp.test.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import assert from 'node:assert/strict';
import test from 'node:test';

import { TOTP_STEP_MS, createTotpCounterReserver, totpCode } from './totp.mjs';

// RFC 6238 appendix B SHA-1 seed "12345678901234567890" in Base32.
const RFC_SECRET = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ';

test('totpCode matches the RFC 6238 SHA-1 test vectors (last six digits)', () => {
    assert.equal(totpCode(RFC_SECRET, Math.floor(59 / 30)), '287082');
    assert.equal(totpCode(RFC_SECRET, Math.floor(1111111109 / 30)), '081804');
    assert.equal(totpCode(RFC_SECRET, Math.floor(20000000000 / 30)), '353130');
});

test('totpCode rejects non-Base32 secrets', () => {
    assert.throws(() => totpCode('not base32!', 1), /Base32/);
});

function fakeClock(startMs) {
    const clock = { nowMs: startMs, sleeps: [] };
    clock.now = () => clock.nowMs;
    clock.sleep = async (ms) => {
        clock.sleeps.push(ms);
        clock.nowMs += ms;
    };
    return clock;
}

test('reserver never hands out the same time step twice, even for concurrent logins', async () => {
    const clock = fakeClock(100 * TOTP_STEP_MS + 1_000);
    const reserve = createTotpCounterReserver(clock);

    const counters = await Promise.all([reserve(), reserve(), reserve()]);

    assert.deepEqual(counters, [100, 101, 102]);
});

test('reserver skips to the next step near the end of the current window', async () => {
    const clock = fakeClock(100 * TOTP_STEP_MS + TOTP_STEP_MS - 1_000);
    const reserve = createTotpCounterReserver(clock);

    const counter = await reserve();

    assert.equal(counter, 101);
    assert.equal(clock.sleeps.length, 1);
    assert.ok(clock.nowMs >= 101 * TOTP_STEP_MS);
});

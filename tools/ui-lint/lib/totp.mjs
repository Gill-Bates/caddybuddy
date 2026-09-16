//
// tools/ui-lint/lib/totp.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import { createHmac } from 'node:crypto';

export const TOTP_STEP_MS = 30_000;
// The server accepts only the current step, so never submit a code this close
// to the end of its window.
const TOTP_STEP_END_GUARD_MS = 3_000;
const BASE32_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';

function decodeBase32(secret) {
    const normalized = String(secret).replace(/[\s=-]/g, '').toUpperCase();
    if (!normalized) {
        throw new Error('TOTP secret is empty.');
    }
    let bits = 0;
    let value = 0;
    const bytes = [];
    for (const character of normalized) {
        const index = BASE32_ALPHABET.indexOf(character);
        if (index === -1) {
            throw new Error('TOTP secret is not valid Base32.');
        }
        value = (value << 5) | index;
        bits += 5;
        if (bits >= 8) {
            bytes.push((value >>> (bits - 8)) & 0xff);
            bits -= 8;
        }
    }
    return Buffer.from(bytes);
}

export function totpCode(secret, counter) {
    const message = Buffer.alloc(8);
    message.writeBigUInt64BE(BigInt(counter));
    const digest = createHmac('sha1', decodeBase32(secret)).update(message).digest();
    const offset = digest[digest.length - 1] & 0x0f;
    return String((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000).padStart(6, '0');
}

/**
 * Hand out each TOTP time step at most once per process: the server rejects a
 * reused step as a replay, and parallel browser logins would otherwise collide.
 */
export function createTotpCounterReserver({
    now = () => Date.now(),
    sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
} = {}) {
    let lastReservedCounter = -1;
    return async function reserveTotpCounter() {
        const nowMs = now();
        let counter = Math.floor(nowMs / TOTP_STEP_MS);
        if (nowMs - counter * TOTP_STEP_MS > TOTP_STEP_MS - TOTP_STEP_END_GUARD_MS) {
            counter += 1;
        }
        counter = Math.max(counter, lastReservedCounter + 1);
        lastReservedCounter = counter;

        const waitMs = counter * TOTP_STEP_MS - now();
        if (waitMs > 0) {
            await sleep(waitMs + 250);
        }
        return counter;
    };
}

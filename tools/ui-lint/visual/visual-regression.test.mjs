//
// tools/ui-lint/visual/visual-regression.test.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import { promises as fs } from 'node:fs';

import { PNG } from 'pngjs';

import {
    buildMaskSelectors,
    compareVisualSnapshot,
    inferViewScope,
    sanitizeVisualName,
} from './visual-regression.mjs';

async function makeTempConfig() {
    const root = await fs.mkdtemp(path.join(os.tmpdir(), 'caddybuddy-ui-lint-visual-'));
    return {
        root,
        config: {
            updateBaselines: false,
            thresholdPercent: 0.5,
            pixelThreshold: 0.1,
            screenshotHeight: 1400,
            fixedNowIso: '2026-05-01T12:00:00Z',
            forceNamedViewport: false,
            skipNetworkIdle: false,
            maskSelectors: [],
            baselineDir: path.join(root, 'baselines'),
            currentDir: path.join(root, 'current'),
            diffDir: path.join(root, 'diff'),
        },
    };
}

async function writeSolidPng(filePath, rgba) {
    const png = new PNG({ width: 2, height: 2 });
    for (let index = 0; index < png.data.length; index += 4) {
        png.data[index] = rgba[0];
        png.data[index + 1] = rgba[1];
        png.data[index + 2] = rgba[2];
        png.data[index + 3] = rgba[3];
    }
    await fs.mkdir(path.dirname(filePath), { recursive: true });
    await fs.writeFile(filePath, PNG.sync.write(png));
}

test('sanitizeVisualName rejects empty names instead of collapsing to view', () => {
    assert.equal(sanitizeVisualName(''), '');
    assert.equal(sanitizeVisualName('   '), '');
    assert.equal(sanitizeVisualName('Mobile Sites'), 'mobile_sites');
});

test('inferViewScope matches whole scope tokens only', () => {
    assert.equal(inferViewScope('mobile-sites-overview'), 'sites');
    assert.equal(inferViewScope('desktop-websites-overview'), null);
    assert.equal(inferViewScope('login-error-desktop'), 'login-error');
});

test('buildMaskSelectors includes current sites textarea selector and login error masks', () => {
    const siteSelectors = buildMaskSelectors('mobile-sites-overview', { maskSelectors: [] });
    assert.ok(siteSelectors.includes('#site-caddy-directives'));
    assert.ok(siteSelectors.includes('#site-caddyfile'));

    const loginSelectors = buildMaskSelectors('desktop-login-error', { maskSelectors: [] });
    assert.ok(loginSelectors.includes('.toast'));
    assert.ok(loginSelectors.includes('.alert'));
});

test('compareVisualSnapshot updates existing baselines when enabled', async (t) => {
    const { root, config } = await makeTempConfig();
    t.after(async () => {
        await fs.rm(root, { recursive: true, force: true });
    });

    const baselinePath = path.join(config.baselineDir, 'sites.png');
    const currentPath = path.join(config.currentDir, 'sites.png');
    await writeSolidPng(baselinePath, [255, 0, 0, 255]);
    await writeSolidPng(currentPath, [0, 128, 0, 255]);

    const result = await compareVisualSnapshot('sites', {
        ...config,
        updateBaselines: true,
    });

    assert.equal(result.pass, true);
    assert.equal(result.reason, 'baseline-updated');
    assert.deepEqual(await fs.readFile(baselinePath), await fs.readFile(currentPath));
});

test('compareVisualSnapshot reports decode errors without assuming Error instances', async (t) => {
    const { root, config } = await makeTempConfig();
    t.after(async () => {
        await fs.rm(root, { recursive: true, force: true });
    });

    await fs.mkdir(config.baselineDir, { recursive: true });
    await fs.mkdir(config.currentDir, { recursive: true });
    await fs.writeFile(path.join(config.baselineDir, 'sites.png'), 'not-a-png');
    await fs.writeFile(path.join(config.currentDir, 'sites.png'), 'not-a-png');

    const result = await compareVisualSnapshot('sites', config);

    assert.equal(result.pass, false);
    assert.equal(result.reason, 'decode-error');
    assert.equal(typeof result.error, 'string');
    assert.ok(result.error.length > 0);
});

test('compareVisualSnapshot rejects invalid screenshot names', async (t) => {
    const { root, config } = await makeTempConfig();
    t.after(async () => {
        await fs.rm(root, { recursive: true, force: true });
    });

    await assert.rejects(compareVisualSnapshot('   ', config), /Invalid screenshot name/);
});

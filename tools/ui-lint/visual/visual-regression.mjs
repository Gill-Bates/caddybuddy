//
// tools/ui-lint/visual/visual-regression.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import { promises as fs } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import pixelmatch from 'pixelmatch';
import { PNG } from 'pngjs';

const MODULE_DIR = path.dirname(fileURLToPath(import.meta.url));
const VISUAL_BASE_DIR = MODULE_DIR;
const VISUAL_ARTIFACTS_DIR = path.join(VISUAL_BASE_DIR, '..', 'test-results', 'visual');
const BASELINE_DIR = path.join(VISUAL_BASE_DIR, 'baselines');
const CURRENT_DIR = path.join(VISUAL_ARTIFACTS_DIR, 'current');
const DIFF_DIR = path.join(VISUAL_ARTIFACTS_DIR, 'diff');

const DEFAULT_THRESHOLD_PERCENT = 0.5;
const DEFAULT_PIXEL_THRESHOLD = 0.1;
const DEFAULT_SCREENSHOT_HEIGHT = 1400;
const DEFAULT_FIXED_NOW_ISO = '2026-05-01T12:00:00Z';
const MASK_STYLE_ATTR = 'data-ui-lint-mask-prev-style';
const MASK_FLAG_ATTR = 'data-ui-lint-mask-applied';

const GLOBAL_DYNAMIC_SELECTORS = [
    '[data-dynamic]',
    '[data-ui-lint-dynamic]',
    '.console-time',
    '.session-time',
    '[data-timestamp]',
    '[data-last-updated]',
    'time[datetime]',
];

const VIEW_DYNAMIC_SELECTORS = {
    dashboard: [
        '.audit-feed',
        '.audit-row__time',
        '.audit-row__user',
        '.table tbody td:last-child',
    ],
    servers: [
        '.status-pill',
        '.table tbody td code',
    ],
    configs: [
        '.config-list .small',
        '.history-stack',
        '.history-item',
    ],
    sites: [
        '.table tbody td:nth-child(3)',
        '.table tbody .badge',
        '.cb-code-block',
    ],
    deployments: [
        '.table tbody td:nth-child(5)',
        '.table tbody .badge',
        '.cb-code-block',
    ],
    queue: [
        '.table tbody td:nth-child(4)',
        '.table tbody .badge',
        '#queueSelectedSiteHint',
    ],
    'api-keys': [
        '.status-pill',
        '.table tbody td code',
        '.table tbody td:nth-child(5)',
    ],
    'audit-logs': [
        '.audit-row__time',
        '.audit-row__summary',
        '.audit-row__details',
    ],
    users: [
        '.table tbody td:last-child',
    ],
};

const asBoolean = (value) => {
    const normalized = String(value || '').trim().toLowerCase();
    return ['1', 'true', 'yes', 'on'].includes(normalized);
};

const asMaskSelectorList = (value) =>
    String(value || '')
        .split(',')
        .map((entry) => entry.trim())
        .filter(Boolean);

const asPositiveInt = (value, fallback) => {
    const parsed = Number.parseInt(String(value || ''), 10);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
};

const sanitizeVisualName = (name) =>
    String(name || 'view')
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9-_]+/g, '_')
        .replace(/^_+|_+$/g, '');

const inferViewScope = (name) => {
    const safeName = sanitizeVisualName(name);
    if (!safeName) return null;
    const scopes = [
        'dashboard',
        'servers',
        'configs',
        'sites',
        'deployments',
        'queue',
        'api-keys',
        'audit-logs',
        'users',
        'profile',
        'login-error',
    ];
    return scopes.find((scope) => safeName.includes(`-${scope}-`) || safeName.startsWith(`${scope}-`) || safeName.includes(scope)) || null;
};

const buildMaskSelectors = (name, config) => {
    const scope = inferViewScope(name);
    const viewSelectors = scope ? (VIEW_DYNAMIC_SELECTORS[scope] || []) : [];
    const merged = [...GLOBAL_DYNAMIC_SELECTORS, ...viewSelectors, ...(config.maskSelectors || [])];
    return [...new Set(merged)];
};

const readPng = (buffer) => PNG.sync.read(buffer);

const inferDeviceFromName = (name) => {
    if (name.startsWith('mobile-')) return 'mobile';
    if (name.startsWith('tablet-')) return 'tablet';
    if (name.startsWith('large-desktop-')) return 'large-desktop';
    return 'desktop';
};

const DEFAULT_VIEWPORTS = {
    desktop: { width: 1440, height: 1100 },
    'large-desktop': { width: 1600, height: 1100 },
    tablet: { width: 1024, height: 1366 },
    mobile: { width: 390, height: 844 },
};

const getViewportForName = (safeName, config) => {
    const device = inferDeviceFromName(safeName);
    const base = DEFAULT_VIEWPORTS[device] || DEFAULT_VIEWPORTS.desktop;
    return {
        width: base.width,
        height: asPositiveInt(config.screenshotHeight, DEFAULT_SCREENSHOT_HEIGHT),
    };
};

export function getVisualRegressionConfig() {
    return {
        enabled: asBoolean(process.env.UI_LINT_VISUAL_REGRESSION),
        updateBaselines: asBoolean(process.env.UI_LINT_VISUAL_UPDATE_BASELINES),
        disableResizeObserver: asBoolean(process.env.UI_LINT_DISABLE_RESIZE_OBSERVER),
        thresholdPercent: Number.parseFloat(process.env.UI_LINT_VISUAL_THRESHOLD_PERCENT || `${DEFAULT_THRESHOLD_PERCENT}`),
        pixelThreshold: Number.parseFloat(process.env.UI_LINT_VISUAL_PIXEL_THRESHOLD || `${DEFAULT_PIXEL_THRESHOLD}`),
        screenshotHeight: asPositiveInt(process.env.UI_LINT_VISUAL_SCREENSHOT_HEIGHT, DEFAULT_SCREENSHOT_HEIGHT),
        fixedNowIso: String(process.env.UI_LINT_VISUAL_FIXED_NOW || DEFAULT_FIXED_NOW_ISO),
        maskSelectors: asMaskSelectorList(process.env.UI_LINT_VISUAL_MASK_SELECTORS),
        baselineDir: BASELINE_DIR,
        currentDir: CURRENT_DIR,
        diffDir: DIFF_DIR,
    };
}

export async function ensureVisualRegressionDirs(config = getVisualRegressionConfig()) {
    for (const dir of [config.baselineDir, config.currentDir, config.diffDir]) {
        await fs.mkdir(dir, { recursive: true });
    }
}

export async function stabilizeVisualSnapshot(page, config = getVisualRegressionConfig()) {
    await page.waitForLoadState('domcontentloaded', { timeout: 30000 });
    await page.waitForLoadState('load', { timeout: 30000 });
    await page.waitForLoadState('networkidle', { timeout: 10000 });

    await page.addStyleTag({
        content: `
            html {
                scrollbar-gutter: stable;
            }

            ::-webkit-scrollbar {
                width: 12px !important;
                height: 12px !important;
            }

            *, *::before, *::after {
                animation: none !important;
                transition: none !important;
                caret-color: transparent !important;
                text-rendering: geometricPrecision !important;
                -webkit-font-smoothing: antialiased !important;
            }

            canvas {
                animation: none !important;
                transition: none !important;
            }

            *:focus,
            *:focus-visible {
                outline: none !important;
                box-shadow: none !important;
            }

            ::selection {
                background: transparent !important;
            }
        `,
    });

    await page.evaluate(async ({ disableResizeObserver, fixedNowIso }) => {
        window.__UI_LINT__ = true;

        const fixedNow = new Date(fixedNowIso).valueOf();
        if (Number.isFinite(fixedNow) && !window.__UI_LINT_DATE_FIXED__) {
            const NativeDate = Date;
            class FixedDate extends NativeDate {
                constructor(...args) {
                    if (args.length === 0) {
                        super(fixedNow);
                    } else {
                        super(...args);
                    }
                }

                static now() {
                    return fixedNow;
                }
            }

            Object.defineProperty(FixedDate, 'name', { value: 'Date' });
            window.Date = FixedDate;
            window.__UI_LINT_DATE_FIXED__ = true;
        }

        window.requestAnimationFrame = (cb) => setTimeout(() => cb(performance.now()), 16);

        if (disableResizeObserver) {
            window.ResizeObserver = class {
                observe() { }
                unobserve() { }
                disconnect() { }
            };
        }

        try {
            await document.fonts.ready;
        } catch {
        }

        const images = [...document.images];
        await Promise.all(images.map((img) => {
            if (img.complete) return Promise.resolve();
            return new Promise((resolve) => {
                img.onload = resolve;
                img.onerror = resolve;
            });
        }));

        document.querySelectorAll('[data-dynamic], [data-ui-lint-dynamic]').forEach((el) => {
            if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) {
                el.value = '—';
            } else {
                el.textContent = '—';
            }
        });

        document.querySelectorAll('canvas').forEach((canvas) => {
            const ctx = canvas.getContext('2d');
            if (ctx) {
                ctx.imageSmoothingEnabled = false;
            }
        });

        try {
            document.querySelectorAll(':hover').forEach((el) => {
                el.blur?.();
            });
        } catch { }

        document.activeElement?.blur?.();
        window.scrollTo(0, 0);
    }, {
        disableResizeObserver: Boolean(config.disableResizeObserver),
        fixedNowIso: config.fixedNowIso,
    });

    await page.mouse.move(0, 0);
    await page.waitForTimeout(120);
}

async function applySmartMasking(page, selectors = []) {
    if (!selectors.length) return;

    await page.evaluate(({ selectors, maskStyleAttr, maskFlagAttr }) => {
        const candidateNodes = new Set();
        const isMaskable = (el) => {
            if (!(el instanceof HTMLElement)) return false;
            const tag = el.tagName.toLowerCase();
            return !['html', 'body', 'main'].includes(tag);
        };

        for (const selector of selectors) {
            try {
                document.querySelectorAll(selector).forEach((el) => {
                    if (isMaskable(el)) candidateNodes.add(el);
                });
            } catch { }
        }

        candidateNodes.forEach((el) => {
            if (el.getAttribute(maskFlagAttr) === '1') return;

            const previousInlineStyle = el.getAttribute('style');
            el.setAttribute(maskStyleAttr, previousInlineStyle ?? '');
            el.setAttribute(maskFlagAttr, '1');

            el.style.setProperty('visibility', 'hidden', 'important');
            el.style.setProperty('caret-color', 'transparent', 'important');
        });
    }, {
        selectors,
        maskStyleAttr: MASK_STYLE_ATTR,
        maskFlagAttr: MASK_FLAG_ATTR,
    });
}

async function restoreSmartMasking(page) {
    await page.evaluate(({ maskStyleAttr, maskFlagAttr }) => {
        const escapedFlag = (window.CSS && typeof window.CSS.escape === 'function')
            ? window.CSS.escape(maskFlagAttr)
            : maskFlagAttr;
        const selector = `[${escapedFlag}="1"]`;

        document.querySelectorAll(selector).forEach((el) => {
            if (!(el instanceof HTMLElement)) return;

            const previousInlineStyle = el.getAttribute(maskStyleAttr);
            if (previousInlineStyle && previousInlineStyle.length > 0) {
                el.setAttribute('style', previousInlineStyle);
            } else {
                el.removeAttribute('style');
            }

            el.removeAttribute(maskStyleAttr);
            el.removeAttribute(maskFlagAttr);
        });
    }, {
        maskStyleAttr: MASK_STYLE_ATTR,
        maskFlagAttr: MASK_FLAG_ATTR,
    });
}

export async function captureVisualSnapshot(page, name, config = getVisualRegressionConfig()) {
    const safeName = sanitizeVisualName(name);
    if (!safeName) {
        throw new Error('Invalid screenshot name for visual regression');
    }

    const currentPath = path.join(config.currentDir, `${safeName}.png`);

    const pageViewport = page.viewportSize();
    const fallback = getViewportForName(safeName, config);
    const viewport = {
        width: pageViewport?.width ?? fallback.width,
        height: pageViewport?.height ?? fallback.height,
    };

    await stabilizeVisualSnapshot(page, config);
    const maskSelectors = buildMaskSelectors(safeName, config);
    await applySmartMasking(page, maskSelectors);

    try {
        await page.screenshot({
            path: currentPath,
            fullPage: false,
            animations: 'disabled',
            clip: {
                x: 0,
                y: 0,
                width: viewport.width,
                height: Math.min(viewport.height, config.screenshotHeight),
            },
        });
    } finally {
        await restoreSmartMasking(page);
    }

    return {
        name: safeName,
        currentPath,
    };
}

export async function compareVisualSnapshot(name, config = getVisualRegressionConfig()) {
    const safeName = sanitizeVisualName(name);
    const baselinePath = path.join(config.baselineDir, `${safeName}.png`);
    const currentPath = path.join(config.currentDir, `${safeName}.png`);
    const diffPath = path.join(config.diffDir, `${safeName}.diff.png`);

    await fs.rm(diffPath, { force: true }).catch(() => { });

    const currentExists = await fs.access(currentPath).then(() => true).catch(() => false);
    if (!currentExists) {
        return {
            name: safeName,
            pass: false,
            reason: 'missing-current',
            baselinePath,
            currentPath,
            diffPath,
        };
    }

    const baselineBuf = await fs.readFile(baselinePath).catch(() => null);
    const currentBuf = await fs.readFile(currentPath);

    if (!baselineBuf) {
        if (config.updateBaselines) {
            await fs.copyFile(currentPath, baselinePath);
            return {
                name: safeName,
                pass: true,
                reason: 'baseline-created',
                baselinePath,
                currentPath,
                diffPath,
                diffPixels: 0,
                totalPixels: 0,
                percent: 0,
            };
        }

        return {
            name: safeName,
            pass: false,
            reason: 'missing-baseline',
            baselinePath,
            currentPath,
            diffPath,
        };
    }

    let baseline, current;
    try {
        baseline = readPng(baselineBuf);
        current = readPng(currentBuf);
    } catch (err) {
        return {
            name: safeName,
            pass: false,
            reason: 'decode-error',
            error: err.message,
            baselinePath,
            currentPath,
            diffPath,
        };
    }

    if (baseline.width !== current.width || baseline.height !== current.height) {
        return {
            name: safeName,
            pass: false,
            reason: 'size-mismatch',
            baselinePath,
            currentPath,
            diffPath,
            baselineSize: { width: baseline.width, height: baseline.height },
            currentSize: { width: current.width, height: current.height },
        };
    }

    const diff = new PNG({ width: baseline.width, height: baseline.height });

    const diffPixels = pixelmatch(
        baseline.data,
        current.data,
        diff.data,
        baseline.width,
        baseline.height,
        {
            threshold: config.pixelThreshold,
            includeAA: false,
        }
    );

    const totalPixels = baseline.width * baseline.height;
    const percent = totalPixels > 0 ? (diffPixels / totalPixels) * 100 : 0;
    const roundedPercent = Number(percent.toFixed(3));
    const pass = roundedPercent <= config.thresholdPercent;

    if (!pass) {
        await fs.writeFile(diffPath, PNG.sync.write(diff));
    }

    return {
        name: safeName,
        pass,
        reason: 'compared',
        baselinePath,
        currentPath,
        diffPath,
        diffPixels,
        totalPixels,
        percent: roundedPercent,
        thresholdPercent: config.thresholdPercent,
        pixelThreshold: config.pixelThreshold,
    };
}

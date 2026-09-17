//
// tools/ui-lint/lib/views.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import { THEMES } from './constants.mjs';


const ALL_VIEW_DEVICES = ['desktop', 'large-desktop', 'tablet', 'mobile'];
// Acceptance contexts for the shared tables: 1200-1599px laptops (where the
// Sites list sits beside the form), 375px and 320px phones, and 200% browser
// zoom. Only the table pages pay for these extra contexts.
export const TABLE_VIEW_EXTRA_DEVICES = ['laptop', 'mobile-se', 'mobile-small', 'zoom-200'];
const LOGIN_FAILURE_DEVICES = ['desktop', 'mobile'];
// Two-factor pages need account state (a pending challenge or enrollment), so
// they run in a dedicated sequential phase on a reduced device matrix.
const TWO_FACTOR_DEVICES = ['desktop', 'mobile'];
const MODAL_VIEW_DEVICES = ['desktop', 'mobile'];


function assertUniqueNames(views, label) {
    const seen = new Set();
    for (const view of views) {
        if (seen.has(view.name)) {
            throw new Error(`[${label}] duplicate view name: ${view.name}`);
        }
        seen.add(view.name);
    }
}

export const LOGIN_FAILURE_VIEW_DEFS = [
    { name: 'login-error', url: '/login', scope: 'login' },
];

// Requires an audit account with two-factor authentication enabled. The
// password step opens the challenge; no code is submitted.
export const TWO_FACTOR_CHALLENGE_VIEW_DEFS = [
    { name: 'login-otp', url: '/login/otp', scope: 'login' },
];

// Opt-in (UI_LINT_TWO_FACTOR_FLOWS=1): the audit enrolls and afterwards
// disables two-factor authentication on an account that has it disabled.
export const TWO_FACTOR_SETUP_VIEW_DEFS = [
    { name: 'settings-two-factor-setup', url: '/settings/two-factor', scope: 'settings' },
];

// The recovery codes are only rendered as the response of the single
// confirmation POST, so this view is captured on desktop only.
export const TWO_FACTOR_RECOVERY_VIEW_DEFS = [
    { name: 'settings-two-factor-recovery', url: '/settings/two-factor/confirm', scope: 'settings' },
];

export const VIEW_DEFS = [
    { name: 'dashboard', url: '/', scope: 'dashboard' },
    { name: 'caddyfile', url: '/caddyfile', scope: 'caddyfile' },
    { name: 'sites', url: '/sites', scope: 'sites', extraDevices: TABLE_VIEW_EXTRA_DEVICES },
    { name: 'ssllabs', url: '/ssl-labs', scope: 'ssllabs', extraDevices: TABLE_VIEW_EXTRA_DEVICES },
    // General shows its two cards side by side from 1200px; the laptop context
    // covers the narrowest two-column width.
    { name: 'settings', url: '/settings', scope: 'settings', extraDevices: ['laptop'] },
    { name: 'settings-security', url: '/settings', scope: 'settings', tab: '#settingsSecurityTab' },
    { name: 'settings-passkey', url: '/settings', scope: 'settings', tab: '#settingsPasskeyTab' },
    { name: 'settings-ssllabs', url: '/settings', scope: 'settings', tab: '#settingsSslLabsTab' },
    { name: 'about', url: '/about', scope: 'about', extraDevices: TABLE_VIEW_EXTRA_DEVICES },
    // `modal` opens that Bootstrap modal (after any `tab` switch) before the
    // analyzers run. `devices` replaces the default device matrix.
    { name: 'confirm-action-modal', url: '/', scope: 'dashboard', modal: '#confirmActionModal', devices: MODAL_VIEW_DEVICES },
    {
        name: 'settings-passkey-modal',
        url: '/settings',
        scope: 'settings',
        tab: '#settingsPasskeyTab',
        modal: '#addPasskeyModal',
        devices: MODAL_VIEW_DEVICES,
    },
    // The site form only moves into its modal below 768px.
    { name: 'sites-form-modal', url: '/sites', scope: 'sites', modal: '#site-form-modal', devices: ['mobile', 'mobile-small'] },
    // Opened directly via bootstrap.Modal, so its iframe stays empty here (populating
    // it requires the Preview button's own form submission, which this audit does
    // not simulate); this still covers the modal chrome and its accessibility.
    {
        name: 'settings-maintenance-preview-modal',
        url: '/settings',
        scope: 'settings',
        modal: '#maintenancePagePreviewModal',
        devices: MODAL_VIEW_DEVICES,
    },
];

/**
 * Expand view definitions across the provided devices and all configured themes.
 */
export function expandViewDefinitions(viewDefs, devices = ALL_VIEW_DEVICES) {
    return viewDefs.flatMap(({ extraDevices = [], devices: ownDevices, ...def }) =>
        [...(ownDevices || devices), ...extraDevices].flatMap((device) =>
            THEMES.map((theme) => ({
                ...def,
                name: `${device}-${def.name}-${theme}`,
                device,
                theme,
            }))
        )
    );
}

export const VIEWS = expandViewDefinitions(VIEW_DEFS);
export const LOGIN_FAILURE_VIEWS = expandViewDefinitions(LOGIN_FAILURE_VIEW_DEFS, LOGIN_FAILURE_DEVICES);
export const TWO_FACTOR_CHALLENGE_VIEWS = expandViewDefinitions(TWO_FACTOR_CHALLENGE_VIEW_DEFS, TWO_FACTOR_DEVICES);
export const TWO_FACTOR_SETUP_VIEWS = expandViewDefinitions(TWO_FACTOR_SETUP_VIEW_DEFS, TWO_FACTOR_DEVICES);
export const TWO_FACTOR_RECOVERY_VIEWS = expandViewDefinitions(TWO_FACTOR_RECOVERY_VIEW_DEFS, ['desktop']);

assertUniqueNames(VIEWS, 'VIEWS');
assertUniqueNames(LOGIN_FAILURE_VIEWS, 'LOGIN_FAILURE_VIEWS');
assertUniqueNames(
    [...TWO_FACTOR_CHALLENGE_VIEWS, ...TWO_FACTOR_SETUP_VIEWS, ...TWO_FACTOR_RECOVERY_VIEWS],
    'TWO_FACTOR_VIEWS',
);

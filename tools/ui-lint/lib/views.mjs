//
// tools/ui-lint/lib/views.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

import { THEMES } from './constants.mjs';


const ALL_VIEW_DEVICES = ['desktop', 'large-desktop', 'tablet', 'mobile'];
const LOGIN_FAILURE_DEVICES = ['desktop', 'mobile'];


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

export const VIEW_DEFS = [
    { name: 'caddyfile', url: '/caddyfile', scope: 'caddyfile' },
    { name: 'sites', url: '/sites', scope: 'sites' },
    { name: 'ssllabs', url: '/ssl-labs', scope: 'ssllabs' },
];

/**
 * Expand view definitions across the provided devices and all configured themes.
 */
export function expandViewDefinitions(viewDefs, devices = ALL_VIEW_DEVICES) {
    return viewDefs.flatMap((def) =>
        devices.flatMap((device) =>
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

assertUniqueNames(VIEWS, 'VIEWS');
assertUniqueNames(LOGIN_FAILURE_VIEWS, 'LOGIN_FAILURE_VIEWS');

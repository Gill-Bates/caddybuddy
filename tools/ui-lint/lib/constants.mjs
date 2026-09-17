//
// tools/ui-lint/lib/constants.mjs
// Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
//

// Shared constants for the UI lint tool.

export const VISUAL_DRIFT_THRESHOLD = 0.0025;
export const SCREENSHOT_SETTLE_MS = 800;
export const TAB_SWITCH_SETTLE_MS = 700;
export const OVERFLOW_TOLERANCE_PX = 6;
export const FOOTER_VIEWPORT_GAP_MIN_PX = 8;
export const SIDEBAR_FOOTER_VIEWPORT_CLEARANCE_MIN_PX = 8;
export const SIDEBAR_NAV_GAP_MIN_PX = 10;
export const SIDEBAR_NAV_LINK_MIN_HEIGHT_PX = 52;
export const SIDEBAR_NAV_GAP_MOBILE_MIN_PX = 7;
export const SIDEBAR_NAV_LINK_MOBILE_MIN_HEIGHT_PX = 48;
export const LAYOUT_SHIFT_THRESHOLD = 0.02;
export const CLICK_TARGET_MIN_SIZE_PX = 44;
export const DENSE_TABLE_CLICK_TARGET_MIN_SIZE_PX = 32;
// Inline chip remove buttons (.tag-input__remove) keep a 20px glyph with a
// pseudo-element hit area. 32px clears WCAG 2.2 AA (24px) without the hit area
// spilling into neighbouring chips, which a full 44px would do.
export const CHIP_REMOVE_CLICK_TARGET_MIN_SIZE_PX = 32;
// iOS Safari auto-zooms into focused form fields rendered below 16px.
export const TOUCH_INPUT_MIN_FONT_SIZE_PX = 16;
// Stack size for the toast exit probe: toasts above the bottom one of a stack
// travel furthest to leave the viewport, so a single toast would not catch it.
export const TOAST_EXIT_PROBE_COUNT = 3;
export const LOGIN_ERROR_SETTLE_MS = 120;
// Above app/utils/hidden_captcha.py DEFAULT_MIN_AGE_SECONDS (1.0): faster auth
// form submissions are rejected as bots with the same 403 as bad credentials.
export const LOGIN_CAPTCHA_MIN_AGE_MS = 1200;
export const LOGIN_LOCKOUT_RESET_MS = 16000;
export const LOGIN_TEST_STAGGER_MS = 13000;
// Single source of truth for login-failure detection, consumed both in the
// Node context (extractLoginFailureTextFromText, matching the raw POST /login
// response body) and in the page context (loginFailureProbeScript, injected
// via page.evaluate). Keep these in sync with the alert markup in
// app/templates/login.html and the failure copy in app/routers/ui/auth.py.
export const LOGIN_FAILURE_ALERT_SELECTORS = Object.freeze([
  '.app-toast-stack .toast[role="status"] .toast-body',
  '.toast[role="status"] .toast-body',
  '.app-toast-stack .toast[role="status"]',
  '.toast[role="status"]',
  '.app-flash-stack .alert[role="alert"]',
  '.alert[role="alert"]',
  '.alert-danger',
  '.login-error',
  '.error-message',
  '[data-testid="login-error"]',
]);
// RegExp source strings (not RegExp instances, so they survive page.evaluate
// argument serialization and JSON round-trips) used as a text fallback when
// no selector above matches.
export const LOGIN_FAILURE_TEXT_PATTERN_SOURCES = Object.freeze([
  'invalid credentials\\.?',
  'too many[^.]*attempts[^.]*\\.?',
  'rate limit[^.]*\\.?',
  'locked[^.]*\\.?',
]);
export const BADGE_FONT_SIZE_TOLERANCE_PX = 0.5;
export const BADGE_PADDING_TOLERANCE_PX = 1;
// Unified compact pill geometry. Single source of truth mirrored from the
// --cb-pill-* CSS tokens (reference pill: .site-cert__days). Every app pill
// must share this height/font-size/padding so badges stay visually uniform.
export const PILL_MIN_HEIGHT_EXPECTED_PX = 21.6; // 1.35rem @ 16px root
export const PILL_FONT_SIZE_EXPECTED_PX = 11.84; // 0.74rem @ 16px root
export const PILL_PADDING_INLINE_EXPECTED_PX = 7.2; // 0.45rem @ 16px root
export const PILL_MIN_HEIGHT_TOLERANCE_PX = 1;
// About page value typography (Application Details / Check for Updates meta
// tables and the Dependencies table) must render at a single shared size.
// Reference: .about-deps-table code (Dependencies panel).
export const ABOUT_VALUE_FONT_SIZE_EXPECTED_PX = 14; // 0.875rem @ 16px root
export const ABOUT_VALUE_FONT_SIZE_TOLERANCE_PX = 0.5;
export const MODAL_DARK_DIALOG_MAX_LUMA = 0.3;
export const MODAL_CONTROL_LIGHT_BG_MIN_LUMA = 0.72;
export const MODAL_CONTROL_DARK_TEXT_MAX_LUMA = 0.3;
export const KPI_CARD_PADDING_EXPECTED = 16;
export const KPI_CARD_PADDING_TOLERANCE = 1;
export const KPI_ICON_MIN = 32;
export const KPI_ICON_MAX = 40;
export const KPI_HEIGHT_TOLERANCE_PX = 2;
export const KPI_ROW_VARIANCE_MAX = 3;
export const KPI_HEIGHT_MAX_DESKTOP_PX = 145;
export const KPI_HEIGHT_MAX_MOBILE_PX = 106;
export const KPI_SIDE_INSET_VARIANCE_MAX_PX = 2;
export const ONBOARDING_WIZARD_ACTIVE_OPACITY_MIN = 0.95;
export const ONBOARDING_WIZARD_INACTIVE_OPACITY_MAX = 0.8;
export const KPI_CARD_REQUIRED_SCOPES = Object.freeze(['dashboard']);
export const DESKTOP_TABLE_HEAD_MIN_FONT_SIZE_PX = 12;
export const DESKTOP_TABLE_CELL_MIN_FONT_SIZE_PX = 14;
export const THEMES = Object.freeze(['light', 'dark']);
export const CONSOLE_LIGHT_BG_MIN_LUMA = 0.75;
// Derived from the Dashboard reference: 1rem page stack gap plus 1.2rem
// header margin. Keep first content surfaces aligned across all app pages.
export const APP_PAGE_HEADER_CONTENT_GAP_EXPECTED_PX = 35.2;
export const APP_PAGE_HEADER_CONTENT_GAP_MOBILE_EXPECTED_PX = 14.4;
export const APP_PAGE_HEADER_CONTENT_GAP_TOLERANCE_PX = 2;
export const APP_PAGE_HEADER_CONTENT_ALIGNMENT_TOLERANCE_PX = 2;
export const PRIMARY_PANEL_PADDING_VARIANCE_MAX_PX = 2;
export const MOBILE_TOGGLE_CONTENT_ALIGNMENT_TOLERANCE_PX = 2;
export const MOBILE_TOPBAR_CLEARANCE_MIN_PX = 56;
export const MOBILE_CARD_HEADING_ALIGNMENT_TOLERANCE_PX = 8;
export const DESKTOP_PRIMARY_PANEL_HEIGHT_TOLERANCE_PX = 3;
// The desktop shell reserves a fixed 1.5rem (24px) footer breathing zone
// below full-height panels (Caddyfile, Sites, SSL Labs) via .app-main's
// padding-bottom in each body:has(.app-page--*) block. Enforce that value
// (not just a ceiling) so the three pages cannot silently drift apart again.
export const DESKTOP_VIEWPORT_PANEL_FOOTER_GAP_EXPECTED_PX = 24;
export const DESKTOP_VIEWPORT_PANEL_FOOTER_GAP_TOLERANCE_PX = 4;
export const SITES_FORM_CONTROL_HEIGHT_EXPECTED_PX = 50;
export const SITES_FORM_CONTROL_HEIGHT_TOLERANCE_PX = 2;
export const SITES_FORM_CONFIG_EDITOR_BOTTOM_GAP_MAX_PX = 16;
export const SITES_FORM_CONFIG_ACTIONS_GAP_MAX_PX = 20;
// Shared table sizing contract (Sites, SSL Labs, About). Ordinary single-line
// rows target 44-48px; multi-domain or error rows may grow to the maximum.
export const MANAGEMENT_TABLE_ROW_TARGET_PX = 48;
export const MANAGEMENT_TABLE_ROW_TOLERANCE_PX = 2;
export const MANAGEMENT_TABLE_ROW_MAX_HEIGHT_PX = 72;
// Must match the touch-sizing media query in app/static/css/app.css.
export const TABLE_TOUCH_SIZING_MEDIA_QUERY = '(max-width: 1199.98px), (hover: none), (pointer: coarse)';
export const TABLE_CONTROL_SIZE_PX = 32;
export const TABLE_TOOLBAR_SIZE_PX = 34;
export const TABLE_ACTION_GAP_PX = 6;
export const TABLE_ACTION_GAP_TOUCH_PX = 8;
// Read-only About tables reuse --cb-table-cell-padding-block (0.375rem).
export const TABLE_CELL_PADDING_BLOCK_PX = 6;
export const TABLE_RHYTHM_TOLERANCE_PX = 1;
// Maintenance page editor toolbar (Settings -> General): every tool is the same
// square, compact with a precise pointer on wide screens (switching on
// TABLE_TOUCH_SIZING_MEDIA_QUERY like the tables) and CLICK_TARGET_MIN_SIZE_PX
// otherwise. Tool groups wrap as a whole and never split across lines.
export const EDITOR_TOOL_SIZE_PX = 36;
export const SSLLABS_DOMAIN_CARD_SUMMARY_HEIGHT_MAX_PX = 56;
// Compact desktop controls plus the card divider/padding keep the filter row
// comfortably below 60px. Touch contexts retain 44px controls.
export const SSLLABS_FILTERBAR_HEIGHT_MAX_PX = 60;
// On mobile the SSL Labs site rows must collapse into standalone cards
// (matching the Sites list). A non-trivial corner radius is the cheapest
// reliable signal that the card treatment is applied rather than a flat
// table row.
export const SSLLABS_MOBILE_CARD_MIN_BORDER_RADIUS_PX = 8;
export const SSLLABS_INLINE_SCHEDULER_MIN_WIDTH_PX = 220;
export const SSLLABS_INLINE_SCHEDULER_MAX_WIDTH_PX = 340;
export const SSLLABS_INLINE_SCHEDULER_ALIGNMENT_TOLERANCE_PX = 2;
export const SSLLABS_RETENTION_SCALE_WIDTH_TOLERANCE_PX = 2;
export const SSLLABS_RETENTION_EDGE_ALIGNMENT_TOLERANCE_PX = 2;
export const SSLLABS_RETENTION_SPACING_VARIANCE_TOLERANCE_PX = 2;
export const MD_BREAKPOINT_PX = 768;
export const LG_BREAKPOINT_PX = 992;
export const XL_BREAKPOINT_PX = 1200;

export const WCAG_CONTRAST = Object.freeze({
  NORMAL_AA: 4.5,
  LARGE_AA: 3.0,
  LARGE_TEXT_SIZE_PX: 24,
  LARGE_TEXT_SIZE_BOLD_PX: 18.66,
  BOLD_WEIGHT: 700,
});

export const MOTION_RESET_CSS = `
  *, *::before, *::after {
    animation: none !important;
    transition: none !important;
    scroll-behavior: auto !important;
    caret-color: transparent !important;
  }
`;

export const FULL_MOTION_RESET_CSS = MOTION_RESET_CSS;

// Only pass browser-consumed constants through CDP; Node-side findings import directly.
export const UI_EVAL_CONSTANTS = Object.freeze({
  OVERFLOW_TOLERANCE_PX,
  FOOTER_VIEWPORT_GAP_MIN_PX,
  SIDEBAR_FOOTER_VIEWPORT_CLEARANCE_MIN_PX,
  SIDEBAR_NAV_GAP_MIN_PX,
  SIDEBAR_NAV_LINK_MIN_HEIGHT_PX,
  SIDEBAR_NAV_GAP_MOBILE_MIN_PX,
  SIDEBAR_NAV_LINK_MOBILE_MIN_HEIGHT_PX,
  SITES_FORM_CONTROL_HEIGHT_EXPECTED_PX,
  SITES_FORM_CONTROL_HEIGHT_TOLERANCE_PX,
  SITES_FORM_CONFIG_EDITOR_BOTTOM_GAP_MAX_PX,
  SITES_FORM_CONFIG_ACTIONS_GAP_MAX_PX,
  MANAGEMENT_TABLE_ROW_TARGET_PX,
  MANAGEMENT_TABLE_ROW_TOLERANCE_PX,
  MANAGEMENT_TABLE_ROW_MAX_HEIGHT_PX,
  TABLE_TOUCH_SIZING_MEDIA_QUERY,
  TABLE_CONTROL_SIZE_PX,
  TABLE_TOOLBAR_SIZE_PX,
  TABLE_ACTION_GAP_PX,
  TABLE_ACTION_GAP_TOUCH_PX,
  TABLE_CELL_PADDING_BLOCK_PX,
  TABLE_RHYTHM_TOLERANCE_PX,
  EDITOR_TOOL_SIZE_PX,
  SSLLABS_DOMAIN_CARD_SUMMARY_HEIGHT_MAX_PX,
  SSLLABS_FILTERBAR_HEIGHT_MAX_PX,
  SSLLABS_MOBILE_CARD_MIN_BORDER_RADIUS_PX,
  SSLLABS_INLINE_SCHEDULER_MIN_WIDTH_PX,
  SSLLABS_INLINE_SCHEDULER_MAX_WIDTH_PX,
  SSLLABS_INLINE_SCHEDULER_ALIGNMENT_TOLERANCE_PX,
  SSLLABS_RETENTION_SCALE_WIDTH_TOLERANCE_PX,
  SSLLABS_RETENTION_EDGE_ALIGNMENT_TOLERANCE_PX,
  SSLLABS_RETENTION_SPACING_VARIANCE_TOLERANCE_PX,
  KPI_HEIGHT_MAX_DESKTOP_PX,
  KPI_HEIGHT_MAX_MOBILE_PX,
  KPI_SIDE_INSET_VARIANCE_MAX_PX,
  ONBOARDING_WIZARD_ACTIVE_OPACITY_MIN,
  ONBOARDING_WIZARD_INACTIVE_OPACITY_MAX,
  PILL_MIN_HEIGHT_EXPECTED_PX,
  PILL_FONT_SIZE_EXPECTED_PX,
  PILL_PADDING_INLINE_EXPECTED_PX,
  PILL_MIN_HEIGHT_TOLERANCE_PX,
  BADGE_FONT_SIZE_TOLERANCE_PX,
  BADGE_PADDING_TOLERANCE_PX,
  ABOUT_VALUE_FONT_SIZE_EXPECTED_PX,
  ABOUT_VALUE_FONT_SIZE_TOLERANCE_PX,
  CLICK_TARGET_MIN_SIZE_PX,
  DENSE_TABLE_CLICK_TARGET_MIN_SIZE_PX,
  CHIP_REMOVE_CLICK_TARGET_MIN_SIZE_PX,
  TOUCH_INPUT_MIN_FONT_SIZE_PX,
  TOAST_EXIT_PROBE_COUNT,
  CONSOLE_LIGHT_BG_MIN_LUMA,
  MD_BREAKPOINT_PX,
  LG_BREAKPOINT_PX,
  XL_BREAKPOINT_PX,
  APP_PAGE_HEADER_CONTENT_GAP_EXPECTED_PX,
  APP_PAGE_HEADER_CONTENT_GAP_MOBILE_EXPECTED_PX,
  APP_PAGE_HEADER_CONTENT_GAP_TOLERANCE_PX,
  APP_PAGE_HEADER_CONTENT_ALIGNMENT_TOLERANCE_PX,
  PRIMARY_PANEL_PADDING_VARIANCE_MAX_PX,
  MOBILE_TOGGLE_CONTENT_ALIGNMENT_TOLERANCE_PX,
  MOBILE_TOPBAR_CLEARANCE_MIN_PX,
  MOBILE_CARD_HEADING_ALIGNMENT_TOLERANCE_PX,
  DESKTOP_PRIMARY_PANEL_HEIGHT_TOLERANCE_PX,
  DESKTOP_VIEWPORT_PANEL_FOOTER_GAP_EXPECTED_PX,
  DESKTOP_VIEWPORT_PANEL_FOOTER_GAP_TOLERANCE_PX,
  MODAL_DARK_DIALOG_MAX_LUMA,
  MODAL_CONTROL_LIGHT_BG_MIN_LUMA,
  MODAL_CONTROL_DARK_TEXT_MAX_LUMA,
  WCAG_NORMAL_AA: WCAG_CONTRAST.NORMAL_AA,
});

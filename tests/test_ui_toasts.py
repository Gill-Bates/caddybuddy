#!/usr/bin/env python3
#
# tests/test_ui_toasts.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

from pathlib import Path


def test_toasts_use_a_shadowed_transform_only_slide_transition() -> None:
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    start = css.index(".app-toast-stack .toast.toast-slide {")
    toast_css = css[start : css.index("@media (max-width: 575.98px)", start)]

    assert "box-shadow: var(--cb-shadow-sm);" in toast_css
    assert "will-change: transform;" in toast_css
    assert "opacity:" not in toast_css
    assert "transform: translate3d(var(--cb-toast-exit-x, calc(100% + 2.5rem + env(safe-area-inset-right, 0px))), 0, 0);" in toast_css
    assert "transition: transform var(--cb-duration-slide) cubic-bezier(0.22, 1, 0.36, 1);" in toast_css
    assert ".app-toast-stack .toast.toast-slide.is-entered {\n    transform: translate3d(0, 0, 0);\n}" in toast_css
    # Bootstrap's .show/.showing are set together during both show and hide, so
    # the slide must not be keyed off them.
    assert ".toast-slide.show" not in css
    assert ".toast-slide.showing" not in css


def test_toast_markup_uses_the_shared_shadow_style() -> None:
    flashes = Path("app/templates/partials/flashes.html").read_text(encoding="utf-8")
    app_core = Path("app/static/js/app-core.js").read_text(encoding="utf-8")

    assert "shadow-sm" not in flashes
    assert "shadow-sm" not in app_core
    assert "toast toast-slide" in flashes
    assert "toast toast-slide" in app_core

def test_toast_stack_is_anchored_to_the_bottom_of_the_viewport() -> None:
    """Top-right toasts covered .app-page__header (status pill, action buttons)
    and the fixed .mobile-topbar, so the stack is bottom-anchored instead."""
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    start = css.index(".app-toast-stack {")
    stack_css = css[start : css.index("}", start)]

    assert "bottom: max(1rem, env(safe-area-inset-bottom)) !important;" in stack_css
    assert "top:" not in stack_css
    assert "z-index: var(--cb-z-toast);" in stack_css
    assert "--cb-z-toast: 1095;" in css

    flashes = Path("app/templates/partials/flashes.html").read_text(encoding="utf-8")
    app_core = Path("app/static/js/app-core.js").read_text(encoding="utf-8")
    container_classes = "toast-container app-toast-stack position-fixed bottom-0 end-0 p-3"

    assert container_classes in flashes
    assert container_classes in app_core
    assert "top-0 end-0" not in flashes


def test_toast_close_button_meets_the_pointer_target_size() -> None:
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    start = css.index(".app-toast-stack .btn-close {")
    close_css = css[start : css.index("}", start)]

    assert "width: 2.75rem;" in close_css
    assert "height: 2.75rem;" in close_css


def test_toast_auto_dismiss_countdown_pauses_on_hover_and_focus() -> None:
    app_core = Path("app/static/js/app-core.js").read_text(encoding="utf-8")

    assert "bootstrap.Toast" not in app_core
    assert 'for (const eventName of ["mouseenter", "focusin"]) {' in app_core
    assert 'for (const eventName of ["mouseleave", "focusout"]) {' in app_core
    assert "TOAST_RESUME_GRACE_MS" in app_core


def test_toast_close_button_slides_the_toast_out_instead_of_removing_it() -> None:
    flashes = Path("app/templates/partials/flashes.html").read_text(encoding="utf-8")
    app_core = Path("app/static/js/app-core.js").read_text(encoding="utf-8")

    # data-bs-dismiss="toast" would hand the toast to Bootstrap's plugin, whose
    # .showing class hides it instantly instead of letting it slide out.
    assert 'data-bs-dismiss="toast"' not in flashes
    assert "data-toast-dismiss" in flashes
    assert '"data-toast-dismiss"' in app_core
    assert 'classList.remove("is-entered")' in app_core


def test_toast_exit_offset_clears_the_viewport_for_every_stacked_toast() -> None:
    """calc(100% + ...) only covers a toast's own size: the stack padding kept a
    sliver on screen, and toasts above the bottom one of a mobile stack never left
    the viewport. The exact per-toast distance comes from App.setToastExitOffset."""
    css = Path("app/static/css/app.css").read_text(encoding="utf-8")
    app_core = Path("app/static/js/app-core.js").read_text(encoding="utf-8")

    assert "translate3d(0, var(--cb-toast-exit-y, calc(100vh + 1rem)), 0)" in css
    assert "App.setToastExitOffset = (toastElement) => {" in app_core
    assert '"--cb-toast-exit-x"' in app_core
    assert '"--cb-toast-exit-y"' in app_core
    # Measured on enter and again on leave, since toasts below may have gone.
    assert app_core.count("App.setToastExitOffset(toastElement);") == 2
    leave_start = app_core.index("const leave = () => {")
    leave_body = app_core[leave_start : app_core.index("let timerId = 0;", leave_start)]
    assert leave_body.index("App.setToastExitOffset(toastElement);") < leave_body.index('classList.remove("is-entered")')

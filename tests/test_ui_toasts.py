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
    assert "transform: translate3d(calc(100% + 1.25rem), 0, 0);" in toast_css
    assert "transition: transform var(--cb-duration-medium) cubic-bezier(0.22, 1, 0.36, 1);" in toast_css
    assert ".app-toast-stack .toast.toast-slide.show {\n    transform: translate3d(0, 0, 0);\n}" in toast_css


def test_toast_markup_uses_the_shared_shadow_style() -> None:
    flashes = Path("app/templates/partials/flashes.html").read_text(encoding="utf-8")
    app_core = Path("app/static/js/app-core.js").read_text(encoding="utf-8")

    assert "shadow-sm" not in flashes
    assert "shadow-sm" not in app_core
    assert "toast toast-slide" in flashes
    assert "toast toast-slide" in app_core

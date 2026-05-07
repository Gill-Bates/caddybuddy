#!/usr/bin/env python3
#
# app/utils/banner.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Startup banner helpers for CaddyBuddy."""

import fcntl
import os
import sys
import tempfile
import time
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_VERSION_FILE = _PROJECT_ROOT / "VERSION"
_BUILD_INFO_FILE = _PROJECT_ROOT / "BUILD_INFO"
_BANNER_DEDUP_WINDOW_SECONDS = 30.0
_LOCK_FLAGS = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
_LOCK_MODE = 0o600


def _lock_file_path() -> Path:
    """Return the per-user lock file path used for banner deduplication."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    base_dir = Path(runtime_dir) if runtime_dir else Path(tempfile.gettempdir())
    return base_dir / f"caddybuddy_banner_{os.getuid()}.lock"


_BANNER_LOCK_FILE = _lock_file_path()


def _read_text(path: Path, default: str) -> str:
    """Return stripped file contents, or ``default`` if unreadable or empty."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return default
    return text or default


def _load_version_info() -> tuple[str, str]:
    """Return version and build metadata for the startup banner."""
    version = _read_text(_VERSION_FILE, "dev")
    build_info = _read_text(_BUILD_INFO_FILE, "working-tree")
    return version, build_info


def _supports_color() -> bool:
    """Return ``True`` when ANSI color output is appropriate."""
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def print_banner() -> None:
    """Print the CaddyBuddy startup banner."""
    version, build_info = _load_version_info()
    build_short = build_info[:7] if build_info else "dev"

    ascii_art = r"""
               _     _       _               _     _       
  ___ __ _  __| | __| |_   _| |__  _   _  __| | __| |_   _ 
 / __/ _` |/ _` |/ _` | | | | '_ \| | | |/ _` |/ _` | | | |
| (_| (_| | (_| | (_| | |_| | |_) | |_| | (_| | (_| | |_| |
 \___\__,_|\__,_|\__,_|\__, |_.__/ \__,_|\__,_|\__,_|\__, |
                       |___/                         |___/ 
""".strip("\n")

    text_lines = [
        f"CaddyBuddy v{version} ({build_short})",
        "Manage Caddy with ease.",
        "(C) 2026 Gill-Bates (https://github.com/Gill-Bates/caddybuddy)",
    ]

    ascii_lines = ascii_art.splitlines()
    ascii_width = max((len(line) for line in ascii_lines), default=0)
    text_width = max((len(line) for line in text_lines), default=0)
    master_width = max(ascii_width, text_width)

    left_pad = max((master_width - ascii_width) // 2, 0)
    ascii_centered = "\n".join((" " * left_pad) + line for line in ascii_lines)
    text_centered = [line.center(master_width) for line in text_lines]
    banner = "\n" + "\n".join([ascii_centered, *text_centered]) + "\n"

    if _supports_color():
        sys.stdout.write(f"\033[96m{banner}\033[0m\n")
    else:
        sys.stdout.write(banner + "\n")

    sys.stdout.flush()


def print_banner_once() -> None:
    """Print the startup banner at most once per process tree.

    Uses an exclusive POSIX file lock so only one worker prints the
    banner during a clustered multi-process startup on Linux.
    """
    process_tree_id = str(os.getppid())

    try:
        fd = os.open(_BANNER_LOCK_FILE, _LOCK_FLAGS, _LOCK_MODE)
    except OSError:
        print_banner()
        return

    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        content = os.read(fd, 64).decode("utf-8", errors="ignore").strip()
        stored_process_tree_id, _, stored_timestamp_text = content.partition(":")

        try:
            stored_timestamp = float(stored_timestamp_text) if stored_timestamp_text else 0.0
        except ValueError:
            stored_timestamp = 0.0

        now = time.time()
        if (
            stored_process_tree_id == process_tree_id
            and (now - stored_timestamp) < _BANNER_DEDUP_WINDOW_SECONDS
        ):
            return

        print_banner()

        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, f"{process_tree_id}:{now}".encode("utf-8"))
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

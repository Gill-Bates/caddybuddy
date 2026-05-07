#!/usr/bin/env python3
#
# run.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import os

import uvicorn

from app.config.logging import build_log_config
from app.config.settings import get_settings
from app.utils.banner import print_banner_once


def main() -> None:
    """Start the CaddyBuddy application server via Uvicorn."""
    if os.environ.get("CADDYBUDDY_BANNER_PRINTED") != "1":
        print_banner_once()
        os.environ["CADDYBUDDY_BANNER_PRINTED"] = "1"

    settings = get_settings()

    # In reload mode, Uvicorn runs a supervisor process that respawns a worker
    # on file changes. The banner flag above is inherited by that subprocess.
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_config=build_log_config(settings.log_level),
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        reload=settings.reload,
    )


if __name__ == "__main__":
    main()
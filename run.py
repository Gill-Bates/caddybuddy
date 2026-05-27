#!/usr/bin/env python3
#
# run.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

import os

import uvicorn

from app.config.logging import build_log_config
from app.config.settings import get_settings
from app.services.events import event_bus
from app.utils.banner import print_banner_once


class CaddyBuddyServer(uvicorn.Server):
    """Uvicorn server that closes SSE subscribers on exit signals."""

    def handle_exit(self, sig: int, frame) -> None:
        event_bus.request_shutdown()
        super().handle_exit(sig, frame)


def main() -> None:
    """Start the CaddyBuddy application server via Uvicorn."""
    if os.environ.get("CADDYBUDDY_BANNER_PRINTED") != "1":
        print_banner_once()
        os.environ["CADDYBUDDY_BANNER_PRINTED"] = "1"

    settings = get_settings()

    # In reload mode, Uvicorn runs a supervisor process that respawns a worker
    # on file changes. The banner flag above is inherited by that subprocess.
    # Exclude the data/ directory so SQLite WAL/SHM churn never triggers reloads.
    reload_excludes = ["data"] if settings.reload else []

    config = uvicorn.Config(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_config=build_log_config(settings.log_level),
        log_level=settings.log_level.lower(),
        proxy_headers=True,
        forwarded_allow_ips=settings.forwarded_allow_ips,
        reload=settings.reload,
        reload_excludes=reload_excludes,
    )
    server = CaddyBuddyServer(config)
    try:
        server.run()
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
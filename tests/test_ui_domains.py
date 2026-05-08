#!/usr/bin/env python3
#
# tests/test_ui_domains.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.routers.ui import domains


class UiDomainsTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_domain_returns_preview_and_validation_errors(self) -> None:
        request = SimpleNamespace(session={})
        current_user = SimpleNamespace(id=1, role="user")

        with (
            patch("app.routers.ui.domains.require_user", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.domains.validated_form",
                new=AsyncMock(
                    return_value={
                        "name": "example.com",
                        "ssl_enabled": "on",
                        "reverse_proxy_options": "transport http {\n    keepalive 30s",
                    }
                ),
            ),
        ):
            response = await domains.preview_domain(request, session=object())

        payload = json.loads(response.body)
        self.assertEqual(response.status_code, 200)
        self.assertIn("example.com {", payload["preview"])
        self.assertEqual(
            payload["errors"],
            [
                "Reverse proxy options require an upstream target.",
                "Reverse proxy options contains unbalanced braces.",
            ],
        )

    async def test_save_domain_rejects_invalid_structured_block_before_repository_access(self) -> None:
        request = SimpleNamespace(session={})
        current_user = SimpleNamespace(id=1, role="admin")

        with (
            patch("app.routers.ui.domains.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.domains.validated_form",
                new=AsyncMock(
                    return_value={
                        "name": "example.com",
                        "header_directives": "header {\n    -Server\n}",
                    }
                ),
            ),
            patch("app.routers.ui.domains.domain_repository.create", new=AsyncMock()) as create_domain,
            patch("app.routers.ui.domains.domain_repository.get_by_name", new=AsyncMock()) as get_by_name,
        ):
            response = await domains.save_domain(request, session=object())

        create_domain.assert_not_awaited()
        get_by_name.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/domains")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": "Header block expects only the inner directives, not the outer header wrapper.",
            }],
        )


if __name__ == "__main__":
    unittest.main()
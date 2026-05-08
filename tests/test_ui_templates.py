#!/usr/bin/env python3
#
# tests/test_ui_templates.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request
from sqlalchemy.exc import IntegrityError

from app.routers.ui import templates
from app.repositories.config_templates import TemplateAlreadyExistsError


def _build_request(path: str = "/templates") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [],
            "query_string": b"",
            "session": {},
            "client": ("127.0.0.1", 12345),
        }
    )


class UiTemplatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_templates_page_loads_live_reference_snapshot_for_server(self) -> None:
        request = SimpleNamespace(session={})
        current_user = SimpleNamespace(id=1, role="admin")
        server = SimpleNamespace(id=7, name="edge-1", active_config_id=11)
        live_config = SimpleNamespace(
            id=11,
            json_config={"apps": {"http": {"servers": {}}}},
            metadata_json={"sites": ["example.com", "www.example.com"]},
        )
        response = SimpleNamespace()

        with (
            patch("app.routers.ui.templates.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.templates.config_template_repository.list_all", new=AsyncMock(return_value=[])),
            patch("app.routers.ui.templates.server_repository.list_all", new=AsyncMock(return_value=[])),
            patch("app.routers.ui.templates.server_repository.get_by_id", new=AsyncMock(return_value=server)),
            patch("app.routers.ui.templates.config_repository.get_by_id", new=AsyncMock(return_value=live_config)),
            patch("app.routers.ui.templates.render_template", return_value=response) as render_template,
        ):
            returned = await templates.templates_page(request, server_id=7, session=object())

        self.assertIs(returned, response)
        context = render_template.call_args.kwargs["context"]
        self.assertIs(context["reference_server"], server)
        self.assertIs(context["reference_live_config"], live_config)
        self.assertEqual(context["reference_live_sites"], ["example.com", "www.example.com"])
        self.assertIn('"apps"', context["reference_live_config_json"])

    async def test_save_template_rejects_oversized_caddyfile_before_validation(self) -> None:
        request = _build_request()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")

        with (
            patch("app.routers.ui.templates.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.templates.validated_form",
                new=AsyncMock(
                    return_value={
                        "name": "huge-template",
                        "caddyfile": "x" * (templates._MAX_CADDYFILE_LENGTH + 1),
                    }
                ),
            ),
            patch("app.routers.ui.templates.deployment_engine.validate_template_for_save", new=AsyncMock()) as validate_template,
            patch("app.routers.ui.templates.config_template_repository.create", new=AsyncMock()) as create_template,
        ):
            response = await templates.save_template(request, session=object())

        validate_template.assert_not_awaited()
        create_template.assert_not_awaited()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/templates")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": f"Caddyfile exceeds the maximum length of {templates._MAX_CADDYFILE_LENGTH} characters.",
            }],
        )

    async def test_save_template_publishes_created_event(self) -> None:
        request = _build_request()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        template = SimpleNamespace(id=5, name="edge")

        with (
            patch("app.routers.ui.templates.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.templates.validated_form",
                new=AsyncMock(return_value={"name": "edge", "caddyfile": "reverse_proxy {{upstream}}"}),
            ),
            patch("app.routers.ui.templates.deployment_engine.validate_template_for_save", new=AsyncMock()),
            patch("app.routers.ui.templates.config_template_repository.create", new=AsyncMock(return_value=template)),
            patch("app.routers.ui.templates.audit_commit_and_flash", new=AsyncMock()) as audit_commit,
            patch("app.routers.ui.templates.publish_resource_event", new=AsyncMock()) as publish_resource_event,
        ):
            response = await templates.save_template(request, session=object())

        audit_commit.assert_awaited_once()
        publish_resource_event.assert_awaited_once_with("config", "created", "5")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/templates/5")

    async def test_save_template_handles_duplicate_checksum_domain_error(self) -> None:
        request = _build_request()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")

        with (
            patch("app.routers.ui.templates.require_admin", new=AsyncMock(return_value=current_user)),
            patch(
                "app.routers.ui.templates.validated_form",
                new=AsyncMock(return_value={"name": "edge", "caddyfile": "reverse_proxy {{upstream}}"}),
            ),
            patch("app.routers.ui.templates.deployment_engine.validate_template_for_save", new=AsyncMock()),
            patch(
                "app.routers.ui.templates.config_template_repository.create",
                new=AsyncMock(side_effect=TemplateAlreadyExistsError("A template with identical Caddyfile content already exists.")),
            ),
        ):
            response = await templates.save_template(request, session=SimpleNamespace(rollback=AsyncMock()))

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/templates")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": "A template with identical Caddyfile content already exists.",
            }],
        )

    async def test_delete_template_handles_integrity_race_cleanly(self) -> None:
        request = _build_request("/templates/5/delete")
        session = SimpleNamespace(rollback=AsyncMock())
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        template = SimpleNamespace(id=5, name="edge", sites=[])

        with (
            patch("app.routers.ui.templates.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.templates.validated_form", new=AsyncMock(return_value={})),
            patch("app.routers.ui.templates.config_template_repository.get_by_id", new=AsyncMock(return_value=template)),
            patch(
                "app.routers.ui.templates.config_template_repository.delete",
                new=AsyncMock(side_effect=IntegrityError("stmt", "params", Exception("restricted"))),
            ),
        ):
            response = await templates.delete_template(request, template_id=5, session=session)

        session.rollback.assert_awaited_once()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/templates/5")
        self.assertEqual(
            request.session["flashes"],
            [{
                "category": "danger",
                "message": "Cannot delete template - it is now referenced by one or more sites.",
            }],
        )


if __name__ == "__main__":
    unittest.main()
#!/usr/bin/env python3
#
# tests/test_ui_queue.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from starlette.requests import Request

from app.models.entities import DeploymentStatus


def _build_request(path: str, *, method: str = "POST") -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [],
            "query_string": b"",
            "session": {},
            "client": ("127.0.0.1", 12345),
        }
    )


class PendingDeploymentQueryTests(unittest.IsolatedAsyncioTestCase):
    """Test site repository pending deployment methods."""

    async def test_list_pending_deployment_returns_sites_never_deployed(self) -> None:
        """Sites with no deployments should appear in pending list."""
        from app.repositories.sites import SiteRepository

        mock_session = AsyncMock()
        mock_site = MagicMock()
        mock_site.id = 1
        mock_site.domain = "example.com"
        mock_site.updated_at = datetime.now(UTC)
        mock_site.deployments = []

        mock_result = MagicMock()
        mock_result.scalars.return_value.unique.return_value.all.return_value = [mock_site]
        mock_session.execute = AsyncMock(return_value=mock_result)

        repo = SiteRepository()
        sites = await repo.list_pending_deployment(mock_session)

        self.assertEqual(len(sites), 1)
        self.assertEqual(sites[0].domain, "example.com")
        statement = mock_session.execute.await_args.args[0]
        self.assertIn("sites.enabled IS true", str(statement))

    async def test_count_pending_deployment_returns_zero_for_empty_db(self) -> None:
        """Empty database should return zero pending count."""
        from app.repositories.sites import SiteRepository

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        mock_session.execute = AsyncMock(return_value=mock_result)

        repo = SiteRepository()
        count = await repo.count_pending_deployment(mock_session)

        self.assertEqual(count, 0)
        statement = mock_session.execute.await_args.args[0]
        self.assertIn("sites.enabled IS true", str(statement))


class QueueRouterTests(unittest.IsolatedAsyncioTestCase):
    """Test queue router helper functions."""

    async def test_queue_page_sets_explicit_csrf_token_in_context(self) -> None:
        from app.routers.ui import queue

        request = _build_request("/queue", method="GET")
        session = AsyncMock()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        pending_site = SimpleNamespace(id=7, domain="example.com", deployments=[])
        server = SimpleNamespace(id=3, name="prod", active=True)

        with (
            patch("app.routers.ui.queue.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.queue.site_repository.list_pending_deployment", new=AsyncMock(return_value=[pending_site])),
            patch("app.routers.ui.queue.server_repository.list_all", new=AsyncMock(return_value=[server])),
            patch("app.routers.ui.queue.ensure_csrf_token", return_value="csrf-123"),
            patch("app.routers.ui.queue.render_template", return_value="rendered") as render_template,
        ):
            response = await queue.queue_page(request, session=session)

        self.assertEqual(response, "rendered")
        render_context = render_template.call_args.kwargs["context"]
        self.assertEqual(render_context["csrf_token"], "csrf-123")

    async def test_queue_page_loads_all_pending_sites_without_hard_limit(self) -> None:
        from app.routers.ui import queue

        request = _build_request("/queue", method="GET")
        session = AsyncMock()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        pending_site = SimpleNamespace(id=7, domain="example.com", deployments=[])

        with (
            patch("app.routers.ui.queue.require_user", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.queue.site_repository.list_pending_deployment", new=AsyncMock(return_value=[pending_site])) as list_pending,
            patch("app.routers.ui.queue.server_repository.list_all", new=AsyncMock(return_value=[])),
            patch("app.routers.ui.queue.ensure_csrf_token", return_value="csrf-123"),
            patch("app.routers.ui.queue.render_template", return_value="rendered"),
        ):
            await queue.queue_page(request, session=session)

        list_pending.assert_awaited_once_with(session)

    def test_get_latest_deployment_returns_none_for_no_deployments(self) -> None:
        """Sites with no deployments should return None."""
        from app.routers.ui.queue import _get_latest_deployment

        mock_site = MagicMock()
        mock_site.deployments = []

        result = _get_latest_deployment(mock_site)
        self.assertIsNone(result)

    def test_get_latest_deployment_returns_none_for_non_deployed_status(self) -> None:
        """Sites with only pending/failed deployments should return None."""
        from app.routers.ui.queue import _get_latest_deployment

        mock_deployment = MagicMock()
        mock_deployment.status = DeploymentStatus.PENDING

        mock_site = MagicMock()
        mock_site.deployments = [mock_deployment]

        result = _get_latest_deployment(mock_site)
        self.assertIsNone(result)

    def test_get_latest_deployment_returns_latest_deployed(self) -> None:
        """Should return the most recent deployed deployment."""
        from app.routers.ui.queue import _get_latest_deployment

        older_deployment = MagicMock()
        older_deployment.status = DeploymentStatus.DEPLOYED
        older_deployment.deployed_at = datetime.now(UTC) - timedelta(days=1)
        older_deployment.deployed_by = "user1"
        older_deployment.server = MagicMock(name="server1")
        older_deployment.server.name = "server1"

        newer_deployment = MagicMock()
        newer_deployment.status = DeploymentStatus.DEPLOYED
        newer_deployment.deployed_at = datetime.now(UTC)
        newer_deployment.deployed_by = "user2"
        newer_deployment.server = MagicMock(name="server2")
        newer_deployment.server.name = "server2"

        mock_site = MagicMock()
        mock_site.deployments = [older_deployment, newer_deployment]

        result = _get_latest_deployment(mock_site)

        self.assertIsNotNone(result)
        self.assertEqual(result["server_name"], "server2")
        self.assertEqual(result["deployed_by"], "user2")

    async def test_deploy_single_site_commits_audit_without_extra_publish(self) -> None:
        from app.routers.ui import queue

        request = _build_request("/queue/deploy/7")
        session = AsyncMock()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        site = SimpleNamespace(id=7, domain="example.com")
        server = SimpleNamespace(id=3, name="prod")
        result = SimpleNamespace(success=True)

        with (
            patch("app.routers.ui.queue.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.queue.validated_form", new=AsyncMock(return_value={"server_id": "3"})),
            patch("app.routers.ui.queue.site_repository.get_by_id", new=AsyncMock(return_value=site)) as get_site,
            patch("app.routers.ui.queue.server_repository.get_by_id", new=AsyncMock(return_value=server)),
            patch("app.routers.ui.queue.deployment_engine.deploy", new=AsyncMock(return_value=result)),
            patch("app.routers.ui.queue.audit_commit_and_flash", new=AsyncMock()) as audit_commit,
        ):
            response = await queue.deploy_single_site(request, site_id=7, session=session)

        get_site.assert_awaited_once_with(session, 7, for_update=True)
        audit_commit.assert_awaited_once()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/queue")

    async def test_deploy_all_pending_rolls_back_failed_sites_and_records_errors(self) -> None:
        from app.routers.ui import queue

        request = _build_request("/queue/deploy-all")
        session = AsyncMock()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        server = SimpleNamespace(id=3, name="prod")
        pending_sites = [
            SimpleNamespace(id=11, domain="ok.example"),
            SimpleNamespace(id=12, domain="fail.example"),
        ]
        loaded_sites = {
            11: SimpleNamespace(id=11, domain="ok.example"),
            12: SimpleNamespace(id=12, domain="fail.example"),
        }
        success_result = SimpleNamespace(success=True)
        failed_result = SimpleNamespace(success=False, error="caddy failed", message="deploy failed")

        with (
            patch("app.routers.ui.queue.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.queue.validated_form", new=AsyncMock(return_value={"server_id": "3"})),
            patch("app.routers.ui.queue.server_repository.get_by_id", new=AsyncMock(return_value=server)),
            patch("app.routers.ui.queue.site_repository.list_pending_deployment", new=AsyncMock(return_value=pending_sites)),
            patch(
                "app.routers.ui.queue.site_repository.get_by_id",
                new=AsyncMock(side_effect=lambda _session, site_id, for_update=False: loaded_sites[site_id]),
            ) as get_site,
            patch(
                "app.routers.ui.queue.deployment_engine.deploy",
                new=AsyncMock(side_effect=[success_result, failed_result]),
            ),
            patch("app.routers.ui.queue.audit_commit_and_flash", new=AsyncMock()) as audit_commit,
        ):
            response = await queue.deploy_all_pending(request, session=session)

        session.rollback.assert_awaited_once()
        session.commit.assert_awaited_once()
        get_site.assert_any_await(session, 11, for_update=True)
        get_site.assert_any_await(session, 12, for_update=True)
        audit_details = audit_commit.await_args.kwargs["details"]
        self.assertEqual(audit_details["success_count"], 1)
        self.assertEqual(audit_details["error_count"], 1)
        self.assertEqual(audit_details["errors"], ["fail.example: caddy failed"])
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/queue")
        self.assertEqual(
            request.session["flashes"],
            [
                {"category": "warning", "message": "Deployed 1 sites, 1 failed."},
                {"category": "danger", "message": "fail.example: caddy failed"},
            ],
        )

    async def test_deploy_all_loads_pending_sites_without_hard_limit(self) -> None:
        from app.routers.ui import queue

        request = _build_request("/queue/deploy-all")
        session = AsyncMock()
        current_user = SimpleNamespace(id=1, role="admin", username="alice")
        server = SimpleNamespace(id=3, name="prod")

        with (
            patch("app.routers.ui.queue.require_admin", new=AsyncMock(return_value=current_user)),
            patch("app.routers.ui.queue.validated_form", new=AsyncMock(return_value={"server_id": "3"})),
            patch("app.routers.ui.queue.server_repository.get_by_id", new=AsyncMock(return_value=server)),
            patch("app.routers.ui.queue.site_repository.list_pending_deployment", new=AsyncMock(return_value=[])) as list_pending,
        ):
            response = await queue.deploy_all_pending(request, session=session)

        list_pending.assert_awaited_once_with(session)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/queue")


if __name__ == "__main__":
    unittest.main()

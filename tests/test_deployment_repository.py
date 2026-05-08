#!/usr/bin/env python3
#
# tests/test_deployment_repository.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.models.entities import DeploymentStatus
from app.repositories.deployments import ConfigDriftResult, deployment_repository
from app.services.deployment_state import InvalidStateTransitionError


class _FakeScalarResult:
    def all(self) -> list[object]:
        return []


class _FakeExecuteResult:
    def scalars(self) -> _FakeScalarResult:
        return _FakeScalarResult()


class DeploymentRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_queries_apply_default_limits(self) -> None:
        captured_statements = []

        async def execute(statement):
            captured_statements.append(statement)
            return _FakeExecuteResult()

        session = SimpleNamespace(execute=AsyncMock(side_effect=execute))

        await deployment_repository.list_all(session)
        await deployment_repository.get_deployments_by_site(session, 1)
        await deployment_repository.get_pending_deployments(session)
        await deployment_repository.get_by_site_and_server(session, 1, 2)
        await deployment_repository.get_servers_for_site(session, 1)

        self.assertEqual(len(captured_statements), 5)
        for statement in captured_statements:
            self.assertIsNotNone(statement._limit_clause)
            self.assertIsNotNone(statement._offset_clause)

    async def test_create_rejects_direct_deployed_status(self) -> None:
        session = SimpleNamespace(add=lambda value: value, flush=AsyncMock())

        with self.assertRaisesRegex(ValueError, "cannot be created directly in DEPLOYED"):
            await deployment_repository.create(
                session,
                site_id=1,
                server_id=2,
                rendered_config="example",
                status=DeploymentStatus.DEPLOYED,
            )

        session.flush.assert_not_awaited()

    async def test_update_status_rejects_invalid_transition(self) -> None:
        session = SimpleNamespace(flush=AsyncMock())
        deployment = SimpleNamespace(
            status=DeploymentStatus.PENDING,
            validation_output=None,
            deployment_error=None,
            deployed_checksum=None,
            rendered_checksum="abc123",
            deployed_at=None,
            deployed_by=None,
        )

        with self.assertRaisesRegex(InvalidStateTransitionError, "Invalid state transition: pending → deployed"):
            await deployment_repository.update_status(
                session,
                deployment,
                DeploymentStatus.DEPLOYED,
                deployed_by="alice",
            )

        session.flush.assert_not_awaited()


    async def test_check_config_drift_returns_typed_result(self) -> None:
        deployment = SimpleNamespace(
            id=7,
            rendered_checksum="rendered",
            deployed_checksum="deployed",
            status=SimpleNamespace(value="deployed"),
        )
        session = object()
        original_get_by_id = deployment_repository.get_by_id
        deployment_repository.get_by_id = AsyncMock(return_value=deployment)
        try:
            result = await deployment_repository.check_config_drift(session, 7)
        finally:
            deployment_repository.get_by_id = original_get_by_id

        self.assertEqual(
            result,
            ConfigDriftResult(
                deployment_id=7,
                rendered_checksum="rendered",
                deployed_checksum="deployed",
                has_drift=True,
                status="deployed",
            ),
        )

    async def test_check_config_drift_raises_for_missing_deployment(self) -> None:
        session = object()
        original_get_by_id = deployment_repository.get_by_id
        deployment_repository.get_by_id = AsyncMock(return_value=None)
        try:
            with self.assertRaisesRegex(ValueError, "Deployment 99 not found"):
                await deployment_repository.check_config_drift(session, 99)
        finally:
            deployment_repository.get_by_id = original_get_by_id


if __name__ == "__main__":
    unittest.main()
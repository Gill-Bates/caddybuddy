#!/usr/bin/env python3
#
# tests/test_deployment_state.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.models.entities import DeploymentStatus
from app.services.deployment_state import InvalidStateTransitionError, deployment_state_machine


class DeploymentStateMachineTests(unittest.TestCase):
    def test_get_valid_transitions_returns_immutable_set(self) -> None:
        transitions = deployment_state_machine.get_valid_transitions(DeploymentStatus.PENDING)

        self.assertIsInstance(transitions, frozenset)
        self.assertEqual(
            transitions,
            frozenset({DeploymentStatus.VALIDATING, DeploymentStatus.DEPLOYING}),
        )

    def test_can_retry_is_derived_from_transition_table(self) -> None:
        self.assertTrue(deployment_state_machine.can_retry(DeploymentStatus.INVALID))
        self.assertTrue(deployment_state_machine.can_retry(DeploymentStatus.FAILED))
        self.assertFalse(deployment_state_machine.can_retry(DeploymentStatus.DEPLOYED))

    def test_is_active_includes_rollback_pending_but_not_deployed_or_failed(self) -> None:
        self.assertTrue(deployment_state_machine.is_active(DeploymentStatus.PENDING))
        self.assertTrue(deployment_state_machine.is_active(DeploymentStatus.ROLLBACK_PENDING))
        self.assertFalse(deployment_state_machine.is_active(DeploymentStatus.DEPLOYED))
        self.assertFalse(deployment_state_machine.is_active(DeploymentStatus.FAILED))

    def test_invalid_transition_error_includes_deployment_context(self) -> None:
        deployment = SimpleNamespace(id=42, status=DeploymentStatus.DEPLOYED)

        with self.assertRaisesRegex(
            InvalidStateTransitionError,
            "Invalid state transition for deployment 42: deployed → deploying",
        ):
            deployment_state_machine.start_deployment(deployment)

    def test_mark_deployed_sets_consistent_deployment_fields(self) -> None:
        deployment = SimpleNamespace(
            id=7,
            status=DeploymentStatus.DEPLOYING,
            rendered_checksum="abc123",
            deployed_checksum=None,
            deployed_at=None,
            deployed_by=None,
            deployment_error="old error",
        )

        deployment_state_machine.mark_deployed(deployment, deployed_by="alice")

        self.assertEqual(deployment.status, DeploymentStatus.DEPLOYED)
        self.assertEqual(deployment.deployed_checksum, "abc123")
        self.assertEqual(deployment.deployed_by, "alice")
        self.assertIsNotNone(deployment.deployed_at)
        self.assertIsNone(deployment.deployment_error)

    def test_reset_for_retry_clears_validation_and_error_fields(self) -> None:
        deployment = SimpleNamespace(
            id=8,
            status=DeploymentStatus.FAILED,
            validation_output="bad config",
            deployment_error="connection failed",
        )

        deployment_state_machine.reset_for_retry(deployment)

        self.assertEqual(deployment.status, DeploymentStatus.PENDING)
        self.assertIsNone(deployment.validation_output)
        self.assertIsNone(deployment.deployment_error)

    def test_start_validation_clears_stale_validation_output(self) -> None:
        deployment = SimpleNamespace(
            id=9,
            status=DeploymentStatus.PENDING,
            validation_output="old output",
        )

        deployment_state_machine.start_validation(deployment)

        self.assertEqual(deployment.status, DeploymentStatus.VALIDATING)
        self.assertIsNone(deployment.validation_output)


if __name__ == "__main__":
    unittest.main()
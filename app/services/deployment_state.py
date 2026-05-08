#!/usr/bin/env python3
#
# app/services/deployment_state.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Deployment state machine service.

Valid transitions:

- PENDING -> VALIDATING, DEPLOYING
- VALIDATING -> VALID, INVALID
- VALID -> DEPLOYING
- INVALID -> PENDING
- DEPLOYING -> DEPLOYED, FAILED
- DEPLOYED -> ROLLBACK_PENDING
- FAILED -> PENDING
- ROLLBACK_PENDING -> ROLLED_BACK, FAILED
- ROLLED_BACK -> terminal

This module defines lifecycle rules only. Callers must persist mutations
within a transaction and rely on the ORM version column for optimistic locking.

"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Final

from app.models.entities import Deployment, DeploymentStatus


_EMPTY_TRANSITIONS: Final[frozenset[DeploymentStatus]] = frozenset()
_FAILED_STATUSES: Final[frozenset[DeploymentStatus]] = frozenset({
    DeploymentStatus.INVALID,
    DeploymentStatus.FAILED,
})
_NON_ACTIVE_STATUSES: Final[frozenset[DeploymentStatus]] = frozenset({
    DeploymentStatus.DEPLOYED,
    DeploymentStatus.ROLLED_BACK,
}) | _FAILED_STATUSES

_TRANSITIONS: Final[Mapping[DeploymentStatus, frozenset[DeploymentStatus]]] = MappingProxyType({
    DeploymentStatus.PENDING: frozenset({DeploymentStatus.VALIDATING, DeploymentStatus.DEPLOYING}),
    DeploymentStatus.VALIDATING: frozenset({DeploymentStatus.VALID, DeploymentStatus.INVALID}),
    DeploymentStatus.VALID: frozenset({DeploymentStatus.DEPLOYING}),
    DeploymentStatus.INVALID: frozenset({DeploymentStatus.PENDING}),
    DeploymentStatus.DEPLOYING: frozenset({DeploymentStatus.DEPLOYED, DeploymentStatus.FAILED}),
    DeploymentStatus.DEPLOYED: frozenset({DeploymentStatus.ROLLBACK_PENDING}),
    DeploymentStatus.FAILED: frozenset({DeploymentStatus.PENDING}),
    DeploymentStatus.ROLLBACK_PENDING: frozenset({DeploymentStatus.ROLLED_BACK, DeploymentStatus.FAILED}),
    DeploymentStatus.ROLLED_BACK: frozenset(),
})

_MISSING_STATUSES = set(DeploymentStatus).difference(_TRANSITIONS)
if _MISSING_STATUSES:
    missing = ", ".join(sorted(status.value for status in _MISSING_STATUSES))
    raise RuntimeError(f"Deployment transition table is missing statuses: {missing}")


def _targets_for(current: DeploymentStatus) -> frozenset[DeploymentStatus]:
    try:
        return _TRANSITIONS[current]
    except KeyError as exc:
        raise ValueError(f"Unhandled deployment status: {current!r}") from exc


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(
        self,
        current: DeploymentStatus,
        target: DeploymentStatus,
        deployment_id: int | None = None,
    ) -> None:
        self.deployment_id = deployment_id
        self.current = current
        self.target = target
        prefix = (
            f"Invalid state transition for deployment {deployment_id}: "
            if deployment_id is not None
            else "Invalid state transition: "
        )
        super().__init__(f"{prefix}{current.value} → {target.value}")


class DeploymentStateMachine:
    """State machine for deployment lifecycle management.

    Ensures all state transitions are valid and provides helpers
    for common deployment workflow operations.
    """

    @staticmethod
    def can_transition(current: DeploymentStatus, target: DeploymentStatus) -> bool:
        """Check if a state transition is valid."""
        return target in _targets_for(current)

    @staticmethod
    def validate_transition(
        current: DeploymentStatus,
        target: DeploymentStatus,
        *,
        deployment_id: int | None = None,
    ) -> None:
        """Validate a state transition, raising on invalid."""
        if not DeploymentStateMachine.can_transition(current, target):
            raise InvalidStateTransitionError(current, target, deployment_id)

    @staticmethod
    def get_valid_transitions(current: DeploymentStatus) -> frozenset[DeploymentStatus]:
        """Get all valid target states from current state."""
        return _targets_for(current)

    @staticmethod
    def is_terminal(status: DeploymentStatus) -> bool:
        """Check if a status is a terminal state."""
        return not _targets_for(status)

    @staticmethod
    def is_active(status: DeploymentStatus) -> bool:
        """Check if deployment is in an active (non-terminal, non-error) state."""
        return bool(_targets_for(status)) and status not in _NON_ACTIVE_STATUSES

    @staticmethod
    def is_deployed(status: DeploymentStatus) -> bool:
        """Check if deployment was successfully deployed."""
        return status == DeploymentStatus.DEPLOYED

    @staticmethod
    def is_failed(status: DeploymentStatus) -> bool:
        """Check if deployment is in a failed state."""
        return status in _FAILED_STATUSES

    @staticmethod
    def can_retry(status: DeploymentStatus) -> bool:
        """Check if deployment can be retried."""
        return DeploymentStatus.PENDING in _targets_for(status)

    @staticmethod
    def can_rollback(status: DeploymentStatus) -> bool:
        """Check if deployment can be rolled back."""
        return DeploymentStatus.ROLLBACK_PENDING in _targets_for(status)

    # High-level transition helpers

    def start_validation(self, deployment: Deployment) -> None:
        """Mutate ``deployment`` in place to VALIDATING. Caller must persist it."""
        self.validate_transition(
            deployment.status,
            DeploymentStatus.VALIDATING,
            deployment_id=getattr(deployment, "id", None),
        )
        deployment.status = DeploymentStatus.VALIDATING

    def mark_valid(self, deployment: Deployment, output: str | None = None) -> None:
        """Mutate ``deployment`` in place to VALID. Caller must persist it."""
        self.validate_transition(
            deployment.status,
            DeploymentStatus.VALID,
            deployment_id=getattr(deployment, "id", None),
        )
        deployment.mark_validated(output)

    def mark_invalid(self, deployment: Deployment, error: str) -> None:
        """Mutate ``deployment`` in place to INVALID. Caller must persist it."""
        self.validate_transition(
            deployment.status,
            DeploymentStatus.INVALID,
            deployment_id=getattr(deployment, "id", None),
        )
        deployment.mark_invalid(error)

    def start_deployment(self, deployment: Deployment) -> None:
        """Mutate ``deployment`` in place to DEPLOYING. Caller must persist it."""
        self.validate_transition(
            deployment.status,
            DeploymentStatus.DEPLOYING,
            deployment_id=getattr(deployment, "id", None),
        )
        deployment.mark_deploying()

    def mark_deployed(self, deployment: Deployment, deployed_by: str | None = None) -> None:
        """Mutate ``deployment`` in place to DEPLOYED. Caller must persist it."""
        self.validate_transition(
            deployment.status,
            DeploymentStatus.DEPLOYED,
            deployment_id=getattr(deployment, "id", None),
        )
        deployment.mark_deployed(deployed_by)

    def mark_failed(self, deployment: Deployment, error: str) -> None:
        """Mutate ``deployment`` in place to FAILED. Caller must persist it."""
        self.validate_transition(
            deployment.status,
            DeploymentStatus.FAILED,
            deployment_id=getattr(deployment, "id", None),
        )
        deployment.mark_failed(error)

    def start_rollback(self, deployment: Deployment) -> None:
        """Mutate ``deployment`` in place to ROLLBACK_PENDING. Caller must persist it."""
        self.validate_transition(
            deployment.status,
            DeploymentStatus.ROLLBACK_PENDING,
            deployment_id=getattr(deployment, "id", None),
        )
        deployment.status = DeploymentStatus.ROLLBACK_PENDING

    def mark_rolled_back(self, deployment: Deployment) -> None:
        """Mutate ``deployment`` in place to ROLLED_BACK. Caller must persist it."""
        self.validate_transition(
            deployment.status,
            DeploymentStatus.ROLLED_BACK,
            deployment_id=getattr(deployment, "id", None),
        )
        deployment.status = DeploymentStatus.ROLLED_BACK

    def reset_for_retry(self, deployment: Deployment) -> None:
        """Mutate ``deployment`` in place back to PENDING. Caller must persist it."""
        self.validate_transition(
            deployment.status,
            DeploymentStatus.PENDING,
            deployment_id=getattr(deployment, "id", None),
        )
        deployment.status = DeploymentStatus.PENDING
        deployment.validation_output = None
        deployment.deployment_error = None


deployment_state_machine = DeploymentStateMachine()

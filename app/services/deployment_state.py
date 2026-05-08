#!/usr/bin/env python3
#
# app/services/deployment_state.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Deployment state machine service.

Defines valid state transitions and enforces state machine rules.
This is the single source of truth for deployment lifecycle.

State Machine:
    PENDING → DEPLOYING → DEPLOYED
           ↘ VALIDATING → VALID
                        ↘ INVALID
                                      ↘ FAILED
    DEPLOYED → ROLLBACK_PENDING → ROLLED_BACK

"""

from __future__ import annotations

from collections.abc import Set
from typing import Final

from app.models.entities import Deployment, DeploymentStatus


# Valid state transitions
_TRANSITIONS: Final[dict[DeploymentStatus, Set[DeploymentStatus]]] = {
    DeploymentStatus.PENDING: {DeploymentStatus.VALIDATING, DeploymentStatus.DEPLOYING},
    DeploymentStatus.VALIDATING: {DeploymentStatus.VALID, DeploymentStatus.INVALID},
    DeploymentStatus.VALID: {DeploymentStatus.DEPLOYING},
    DeploymentStatus.INVALID: {DeploymentStatus.PENDING},  # Allow retry after fix
    DeploymentStatus.DEPLOYING: {DeploymentStatus.DEPLOYED, DeploymentStatus.FAILED},
    DeploymentStatus.DEPLOYED: {DeploymentStatus.ROLLBACK_PENDING},
    DeploymentStatus.FAILED: {DeploymentStatus.PENDING},  # Allow retry
    DeploymentStatus.ROLLBACK_PENDING: {DeploymentStatus.ROLLED_BACK, DeploymentStatus.FAILED},
    DeploymentStatus.ROLLED_BACK: set(),  # Terminal state
}


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current: DeploymentStatus, target: DeploymentStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Invalid state transition: {current.value} → {target.value}"
        )


class DeploymentStateMachine:
    """State machine for deployment lifecycle management.

    Ensures all state transitions are valid and provides helpers
    for common deployment workflow operations.
    """

    @staticmethod
    def can_transition(current: DeploymentStatus, target: DeploymentStatus) -> bool:
        """Check if a state transition is valid."""
        return target in _TRANSITIONS.get(current, set())

    @staticmethod
    def validate_transition(current: DeploymentStatus, target: DeploymentStatus) -> None:
        """Validate a state transition, raising on invalid."""
        if not DeploymentStateMachine.can_transition(current, target):
            raise InvalidStateTransitionError(current, target)

    @staticmethod
    def get_valid_transitions(current: DeploymentStatus) -> Set[DeploymentStatus]:
        """Get all valid target states from current state."""
        return _TRANSITIONS.get(current, set())

    @staticmethod
    def is_terminal(status: DeploymentStatus) -> bool:
        """Check if a status is a terminal state."""
        return len(_TRANSITIONS.get(status, set())) == 0

    @staticmethod
    def is_active(status: DeploymentStatus) -> bool:
        """Check if deployment is in an active (non-terminal, non-error) state."""
        return status in {
            DeploymentStatus.PENDING,
            DeploymentStatus.VALIDATING,
            DeploymentStatus.VALID,
            DeploymentStatus.DEPLOYING,
        }

    @staticmethod
    def is_deployed(status: DeploymentStatus) -> bool:
        """Check if deployment was successfully deployed."""
        return status == DeploymentStatus.DEPLOYED

    @staticmethod
    def is_failed(status: DeploymentStatus) -> bool:
        """Check if deployment is in a failed state."""
        return status in {DeploymentStatus.INVALID, DeploymentStatus.FAILED}

    @staticmethod
    def can_retry(status: DeploymentStatus) -> bool:
        """Check if deployment can be retried."""
        return status in {DeploymentStatus.INVALID, DeploymentStatus.FAILED}

    @staticmethod
    def can_rollback(status: DeploymentStatus) -> bool:
        """Check if deployment can be rolled back."""
        return status == DeploymentStatus.DEPLOYED

    # High-level transition helpers

    def start_validation(self, deployment: Deployment) -> None:
        """Transition deployment to VALIDATING state."""
        self.validate_transition(deployment.status, DeploymentStatus.VALIDATING)
        deployment.status = DeploymentStatus.VALIDATING

    def mark_valid(self, deployment: Deployment, output: str | None = None) -> None:
        """Transition deployment to VALID state."""
        self.validate_transition(deployment.status, DeploymentStatus.VALID)
        deployment.mark_validated(output)

    def mark_invalid(self, deployment: Deployment, error: str) -> None:
        """Transition deployment to INVALID state."""
        self.validate_transition(deployment.status, DeploymentStatus.INVALID)
        deployment.mark_invalid(error)

    def start_deployment(self, deployment: Deployment) -> None:
        """Transition deployment to DEPLOYING state."""
        self.validate_transition(deployment.status, DeploymentStatus.DEPLOYING)
        deployment.mark_deploying()

    def mark_deployed(self, deployment: Deployment, deployed_by: str | None = None) -> None:
        """Transition deployment to DEPLOYED state."""
        self.validate_transition(deployment.status, DeploymentStatus.DEPLOYED)
        deployment.mark_deployed(deployed_by)

    def mark_failed(self, deployment: Deployment, error: str) -> None:
        """Transition deployment to FAILED state."""
        self.validate_transition(deployment.status, DeploymentStatus.FAILED)
        deployment.mark_failed(error)

    def start_rollback(self, deployment: Deployment) -> None:
        """Transition deployment to ROLLBACK_PENDING state."""
        self.validate_transition(deployment.status, DeploymentStatus.ROLLBACK_PENDING)
        deployment.status = DeploymentStatus.ROLLBACK_PENDING

    def mark_rolled_back(self, deployment: Deployment) -> None:
        """Transition deployment to ROLLED_BACK state."""
        self.validate_transition(deployment.status, DeploymentStatus.ROLLED_BACK)
        deployment.status = DeploymentStatus.ROLLED_BACK

    def reset_for_retry(self, deployment: Deployment) -> None:
        """Reset a failed deployment to PENDING for retry."""
        if not self.can_retry(deployment.status):
            raise InvalidStateTransitionError(deployment.status, DeploymentStatus.PENDING)
        deployment.status = DeploymentStatus.PENDING
        deployment.validation_output = None
        deployment.deployment_error = None


deployment_state_machine = DeploymentStateMachine()

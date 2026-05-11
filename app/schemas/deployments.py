#!/usr/bin/env python3
#
# app/schemas/deployments.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Pydantic schemas for Deployment entities."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field



class DeploymentStatusEnum(str, Enum):
    """Deployment status for API responses."""

    PENDING = "pending"
    VALIDATING = "validating"
    VALID = "valid"
    INVALID = "invalid"
    DEPLOYING = "deploying"
    DEPLOYED = "deployed"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    ROLLBACK_PENDING = "rollback_pending"
    ROLLED_BACK = "rolled_back"


class DeploymentCreate(BaseModel):
    """Schema for creating a Deployment."""

    site_id: int
    server_id: int


class DeploymentRead(BaseModel):
    """Schema for reading a Deployment."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    site_id: int
    server_id: int
    rendered_config: str
    rendered_checksum: str
    deployed_checksum: str | None
    status: DeploymentStatusEnum
    validation_output: str | None
    deployment_error: str | None
    deployed_at: datetime | None
    deployed_by: str | None
    rollback_deployment_id: int | None
    created_at: datetime
    updated_at: datetime


class DeploymentList(BaseModel):
    """Schema for listing Deployments."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    site_id: int
    site_domain: str | None = None
    server_id: int
    server_name: str | None = None
    status: DeploymentStatusEnum
    deployed_at: datetime | None
    deployed_by: str | None
    created_at: datetime


class DeploymentWithDetails(DeploymentRead):
    """Schema for Deployment with embedded Site and Server details."""

    site_domain: str | None = None
    site_enabled: bool | None = None
    server_name: str | None = None
    server_active: bool | None = None


class DeploymentAction(BaseModel):
    """Schema for deployment actions (validate, deploy, retry, rollback)."""

    action: str = Field(..., pattern="^(validate|deploy|retry|rollback)$")


class DeploymentActionResponse(BaseModel):
    """Schema for deployment action response."""

    success: bool
    deployment_id: int
    status: DeploymentStatusEnum
    message: str
    validation_output: str | None = None
    error: str | None = None


class DeploymentDriftCheck(BaseModel):
    """Schema for config drift check response."""

    deployment_id: int
    rendered_checksum: str
    deployed_checksum: str | None
    has_drift: bool
    status: DeploymentStatusEnum


class DeploymentHistory(BaseModel):
    """Schema for deployment history entry."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    rendered_checksum: str
    status: DeploymentStatusEnum
    deployed_at: datetime | None
    deployed_by: str | None
    is_current: bool = False

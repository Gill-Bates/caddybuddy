#!/usr/bin/env python3
#
# app/schemas/__init__.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Pydantic schemas."""

from app.schemas.config_templates import (
    ConfigRevisionRead,
    ConfigTemplateCreate,
    ConfigTemplateList,
    ConfigTemplateRead,
    ConfigTemplateUpdate,
)
from app.schemas.deployments import (
    DeploymentAction,
    DeploymentActionResponse,
    DeploymentCreate,
    DeploymentDriftCheck,
    DeploymentHistory,
    DeploymentList,
    DeploymentRead,
    DeploymentStatusEnum,
    DeploymentWithDetails,
)
from app.schemas.sites import (
    SiteCreate,
    SiteDeployRequest,
    SiteDeployResponse,
    SiteList,
    SiteRead,
    SiteUpdate,
    SiteWithTemplate,
)
from app.schemas.system import BuildInfoResponse, HealthResponse

__all__ = [
    # Config Templates
    "ConfigRevisionRead",
    "ConfigTemplateCreate",
    "ConfigTemplateList",
    "ConfigTemplateRead",
    "ConfigTemplateUpdate",
    # Deployments
    "DeploymentAction",
    "DeploymentActionResponse",
    "DeploymentCreate",
    "DeploymentDriftCheck",
    "DeploymentHistory",
    "DeploymentList",
    "DeploymentRead",
    "DeploymentStatusEnum",
    "DeploymentWithDetails",
    # Sites
    "SiteCreate",
    "SiteDeployRequest",
    "SiteDeployResponse",
    "SiteList",
    "SiteRead",
    "SiteUpdate",
    "SiteWithTemplate",
    # System
    "BuildInfoResponse",
    "HealthResponse",
]
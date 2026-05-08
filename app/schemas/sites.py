#!/usr/bin/env python3
#
# app/schemas/sites.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Pydantic schemas for Site entities."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator

if TYPE_CHECKING:
    from app.schemas.config_templates import ConfigTemplateRead


class SiteBase(BaseModel):
    """Base schema for Site."""

    domain: str = Field(..., min_length=1, max_length=255)
    config_template_id: int
    enabled: bool = True
    description: str | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    ssl_enabled: bool = True
    ssl_provider: str = Field(default="letsencrypt", max_length=50)

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, v: str) -> str:
        return v.lower().strip()

    @field_validator("ssl_provider")
    @classmethod
    def validate_ssl_provider(cls, v: str) -> str:
        allowed = {"letsencrypt", "zerossl", "manual", "none"}
        if v.lower() not in allowed:
            raise ValueError(f"ssl_provider must be one of: {', '.join(allowed)}")
        return v.lower()


class SiteCreate(SiteBase):
    """Schema for creating a Site."""

    pass


class SiteUpdate(BaseModel):
    """Schema for updating a Site."""

    domain: str | None = Field(None, min_length=1, max_length=255)
    config_template_id: int | None = None
    enabled: bool | None = None
    description: str | None = None
    variables: dict[str, str] | None = None
    ssl_enabled: bool | None = None
    ssl_provider: str | None = Field(None, max_length=50)

    @field_validator("domain")
    @classmethod
    def normalize_domain(cls, v: str | None) -> str | None:
        return v.lower().strip() if v else None

    @field_validator("ssl_provider")
    @classmethod
    def validate_ssl_provider(cls, v: str | None) -> str | None:
        if v is None:
            return None
        allowed = {"letsencrypt", "zerossl", "manual", "none"}
        if v.lower() not in allowed:
            raise ValueError(f"ssl_provider must be one of: {', '.join(allowed)}")
        return v.lower()


class SiteRead(SiteBase):
    """Schema for reading a Site."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class SiteList(BaseModel):
    """Schema for listing Sites."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    domain: str
    config_template_id: int
    config_template_name: str | None = None
    enabled: bool
    ssl_enabled: bool
    deployment_count: int = 0
    last_deployed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class SiteWithTemplate(SiteRead):
    """Schema for Site with embedded ConfigTemplate."""

    config_template: ConfigTemplateRead | None = None


class SiteDeployRequest(BaseModel):
    """Schema for deploying a Site."""

    server_id: int


class SiteDeployResponse(BaseModel):
    """Schema for deployment response."""

    success: bool
    deployment_id: int
    message: str
    validation_output: str | None = None
    error: str | None = None

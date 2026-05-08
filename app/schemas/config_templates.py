#!/usr/bin/env python3
#
# app/schemas/config_templates.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Pydantic schemas for ConfigTemplate entities."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConfigTemplateBase(BaseModel):
    """Base schema for ConfigTemplate."""

    name: str = Field(..., min_length=1, max_length=150)
    description: str | None = None
    caddyfile: str = Field(..., min_length=1)
    variables: dict[str, str] = Field(default_factory=dict)


class ConfigTemplateCreate(ConfigTemplateBase):
    """Schema for creating a ConfigTemplate."""

    pass


class ConfigTemplateUpdate(BaseModel):
    """Schema for updating a ConfigTemplate."""

    name: str | None = Field(None, min_length=1, max_length=150)
    description: str | None = None
    caddyfile: str | None = Field(None, min_length=1)
    variables: dict[str, str] | None = None
    change_summary: str | None = Field(None, max_length=500)


class ConfigTemplateRead(ConfigTemplateBase):
    """Schema for reading a ConfigTemplate."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    checksum: str
    created_at: datetime
    updated_at: datetime


class ConfigTemplateList(BaseModel):
    """Schema for listing ConfigTemplates."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    checksum: str
    site_count: int = 0
    created_at: datetime
    updated_at: datetime


class ConfigRevisionRead(BaseModel):
    """Schema for reading a ConfigRevision."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    template_id: int
    version: int
    caddyfile: str
    checksum: str
    variables: dict[str, str]
    change_summary: str | None
    created_by: str | None
    created_at: datetime



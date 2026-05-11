#!/usr/bin/env python3
#
# app/schemas/config_templates.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Pydantic schemas for ConfigTemplate entities."""

from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


_MAX_DESCRIPTION_LENGTH = 2_000
_MAX_CADDYFILE_LENGTH = 2 * 1024 * 1024
_MAX_VARIABLE_COUNT = 50
_MAX_VARIABLE_KEY_LENGTH = 200
_MAX_VARIABLE_VALUE_LENGTH = 10_000


def _validate_variables(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if len(value) > _MAX_VARIABLE_COUNT:
        raise ValueError(f"Config template variables must not contain more than {_MAX_VARIABLE_COUNT} entries.")
    for key, item in value.items():
        if len(key) > _MAX_VARIABLE_KEY_LENGTH:
            raise ValueError(
                f"Variable key '{key[:50]}...' exceeds {_MAX_VARIABLE_KEY_LENGTH} characters."
            )
        if len(item) > _MAX_VARIABLE_VALUE_LENGTH:
            raise ValueError(
                f"Variable value for key '{key}' exceeds {_MAX_VARIABLE_VALUE_LENGTH} characters."
            )
    return value


class ConfigTemplateBase(BaseModel):
    """Base schema for ConfigTemplate."""

    name: str = Field(..., min_length=1, max_length=150)
    description: str | None = Field(None, max_length=_MAX_DESCRIPTION_LENGTH)
    caddyfile: str = Field(..., min_length=1, max_length=_MAX_CADDYFILE_LENGTH)
    variables: dict[str, str] = Field(default_factory=dict)

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, value: dict[str, str]) -> dict[str, str]:
        validated = _validate_variables(value)
        assert validated is not None
        return validated


class ConfigTemplateCreate(ConfigTemplateBase):
    """Schema for creating a ConfigTemplate."""

    pass


class ConfigTemplateUpdate(BaseModel):
    """Schema for updating a ConfigTemplate."""

    name: str | None = Field(None, min_length=1, max_length=150)
    description: str | None = Field(None, max_length=_MAX_DESCRIPTION_LENGTH)
    caddyfile: str | None = Field(None, min_length=1, max_length=_MAX_CADDYFILE_LENGTH)
    variables: dict[str, str] | None = None
    change_summary: str | None = Field(None, max_length=500)

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _validate_variables(value)


class ConfigTemplateRead(ConfigTemplateBase):
    """Schema for reading a ConfigTemplate."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    checksum: str
    created_at: AwareDatetime
    updated_at: AwareDatetime


class ConfigTemplateList(BaseModel):
    """Schema for listing ConfigTemplates."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    checksum: str
    site_count: int = 0
    created_at: AwareDatetime
    updated_at: AwareDatetime


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
    created_at: AwareDatetime

    @field_validator("variables")
    @classmethod
    def validate_variables(cls, value: dict[str, str]) -> dict[str, str]:
        validated = _validate_variables(value)
        assert validated is not None
        return validated
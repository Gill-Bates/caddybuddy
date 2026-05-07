#!/usr/bin/env python3
#
# app/config/settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from functools import cache
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


EnvironmentName = Literal["development", "staging", "production"]
LogLevelName = Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"]

_INSECURE_SECRET_VALUES = frozenset(
    {
        "",
        "change-me-in-production",
        "change-me-before-production",
        "changeme",
        "replace-me",
    }
)
_INSECURE_ADMIN_PASSWORD_VALUES = frozenset(
    {
        "",
        "admin",
        "change-me",
        "change-me-before-production",
    }
)

_BASE_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = _BASE_DIR / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "CaddyBuddy"
    environment: EnvironmentName = Field(
        default="production",
        validation_alias=AliasChoices("CADDYBUDDY_ENVIRONMENT", "environment"),
    )
    log_level: LogLevelName = Field(
        default="INFO",
        validation_alias=AliasChoices("CADDYBUDDY_LOG_LEVEL", "LOG_LEVEL", "log_level"),
    )
    secret_key: SecretStr = Field(
        default=SecretStr("change-me-in-production"),
        validation_alias=AliasChoices(
            "CADDYBUDDY_SECRET_KEY",
            "SECRET_KEY",
            "caddybuddy_SECRET_KEY",
            "secret_key",
        ),
    )
    timezone: str = Field(default="UTC", validation_alias=AliasChoices("TZ", "timezone"))
    host: str = "0.0.0.0"
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("CADDYBUDDY_PORT", "PORT", "port"),
    )
    forwarded_allow_ips: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices(
            "CADDYBUDDY_FORWARDED_ALLOW_IPS",
            "FORWARDED_ALLOW_IPS",
            "forwarded_allow_ips",
        ),
    )
    database_url: str = "sqlite+aiosqlite:///data/app.db"
    session_cookie_name: str = "caddybuddy_session"
    session_max_age_seconds: int = 60 * 60 * 8
    default_admin_username: str = "admin"
    default_admin_password: SecretStr = Field(
        default=SecretStr("admin"),
        validation_alias=AliasChoices(
            "CADDYBUDDY_ADMIN_PASSWORD",
            "ADMIN_PASSWORD",
        ),
    )
    default_admin_email: str = "admin@example.com"

    @field_validator("environment", mode="before")
    @classmethod
    def _normalize_environment(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("timezone", mode="before")
    @classmethod
    def _validate_timezone(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"'{normalized}' is not a valid IANA timezone.") from exc
        return normalized

    @model_validator(mode="after")
    def _validate_non_development_secrets(self) -> Self:
        if self.environment == "development":
            return self

        secret_key = self.secret_key.get_secret_value().strip()
        if secret_key in _INSECURE_SECRET_VALUES:
            raise ValueError(
                "Non-development deployments must set a strong secret key via "
                "CADDYBUDDY_SECRET_KEY or SECRET_KEY."
            )

        admin_password = self.default_admin_password.get_secret_value().strip()
        if admin_password in _INSECURE_ADMIN_PASSWORD_VALUES:
            raise ValueError(
                "Non-development deployments must set CADDYBUDDY_ADMIN_PASSWORD "
                "or ADMIN_PASSWORD to a non-default value."
            )

        return self

    @property
    def base_dir(self) -> Path:
        return _BASE_DIR

    @property
    def data_dir(self) -> Path:
        return _DATA_DIR


@cache
def get_settings() -> Settings:
    return Settings()
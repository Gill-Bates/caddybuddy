#!/usr/bin/env python3
#
# app/config/settings.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from functools import cache
import logging
from pathlib import Path
import re
from typing import Literal, Self
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
_MIN_SECRET_LENGTH = 32
_MIN_ADMIN_PASSWORD_LENGTH = 8
_SIMPLE_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
_SESSION_COOKIE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_BASE_DIR = Path(__file__).resolve().parents[2]
_DATA_DIR = _BASE_DIR / "data"
DEFAULT_CADDY_ADMIN_URL = "http://localhost:2019"
DEFAULT_CADDYFILE_PATH = Path("/app/Caddyfile")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "CaddyBuddy"
    log_level: LogLevelName = Field(
        default="INFO",
        validation_alias=AliasChoices("CADDYBUDDY_LOG_LEVEL", "LOG_LEVEL", "log_level"),
    )
    secret_key: SecretStr = Field(
        default=SecretStr("change-me-in-production"),
        validation_alias=AliasChoices(
            "CB_SECRET_KEY",
            "CADDYBUDDY_SECRET_KEY",
            "SECRET_KEY",
            "secret_key",
        ),
    )
    password_pepper: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "CADDYBUDDY_PASSWORD_PEPPER",
            "PASSWORD_PEPPER",
            "password_pepper",
        ),
    )
    timezone: str = Field(default="UTC", validation_alias=AliasChoices("TZ", "timezone"))
    host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("CADDYBUDDY_HOST", "HOST", "host"),
    )
    port: int = Field(
        default=8000,
        ge=1,
        le=65535,
        validation_alias=AliasChoices("CADDYBUDDY_PORT", "PORT", "port"),
    )
    reload: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "CADDYBUDDY_RELOAD",
            "RELOAD",
            "reload",
        ),
    )
    forwarded_allow_ips: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices(
            "CADDYBUDDY_FORWARDED_ALLOW_IPS",
            "FORWARDED_ALLOW_IPS",
            "forwarded_allow_ips",
        ),
    )
    database_url: str = Field(
        default="sqlite+aiosqlite:///data/caddybuddy.db",
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )
    config_template_revision_retry_limit: int = Field(
        default=3,
        ge=1,
        le=10,
        validation_alias=AliasChoices(
            "CADDYBUDDY_CONFIG_TEMPLATE_REVISION_RETRY_LIMIT",
            "CONFIG_TEMPLATE_REVISION_RETRY_LIMIT",
            "config_template_revision_retry_limit",
        ),
    )
    config_template_checksum_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=30.0,
        validation_alias=AliasChoices(
            "CADDYBUDDY_CONFIG_TEMPLATE_CHECKSUM_TIMEOUT_SECONDS",
            "CONFIG_TEMPLATE_CHECKSUM_TIMEOUT_SECONDS",
            "config_template_checksum_timeout_seconds",
        ),
    )
    session_https_only: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "CADDYBUDDY_SESSION_HTTPS_ONLY",
            "SESSION_HTTPS_ONLY",
            "session_https_only",
        ),
    )
    session_cookie_samesite: Literal["strict", "lax", "none"] = Field(
        default="lax",
        validation_alias=AliasChoices(
            "CADDYBUDDY_SESSION_SAMESITE",
            "SESSION_SAMESITE",
            "session_cookie_samesite",
        ),
    )
    session_cookie_name: str = "caddybuddy_session"
    session_max_age_seconds: int = Field(default=60 * 60 * 24, ge=1)  # Cookie lifetime (24h)
    session_inactivity_timeout_seconds: int = Field(default=60 * 60, ge=1)  # 60 min inactivity timeout
    session_absolute_timeout_seconds: int = Field(default=60 * 60 * 24, ge=1)  # 24h absolute lifetime
    default_admin_username: str = Field(default="admin", min_length=1)
    default_admin_email: str = Field(default="admin@example.com")

    # Single Caddy server configuration
    caddy_control_mode: Literal["disabled", "systemd", "docker", "script"] = Field(
        default="disabled",
        validation_alias=AliasChoices("CB_CADDY_CONTROL_MODE", "caddy_control_mode"),
    )
    caddy_systemd_unit: str = Field(
        default="caddy",
        validation_alias=AliasChoices("CB_CADDY_SYSTEMD_UNIT", "caddy_systemd_unit"),
    )
    caddy_docker_container: str = Field(
        default="caddy",
        validation_alias=AliasChoices("CB_CADDY_DOCKER_CONTAINER", "caddy_docker_container"),
    )
    caddy_control_script: str = Field(
        default="/app/caddy-control.sh",
        validation_alias=AliasChoices("CB_CADDY_CONTROL_SCRIPT", "caddy_control_script"),
    )
    caddy_control_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias=AliasChoices("CB_CADDY_CONTROL_TIMEOUT_SECONDS", "caddy_control_timeout_seconds"),
    )
    caddy_restart_confirmation_required: bool = Field(
        default=True,
        validation_alias=AliasChoices("CB_CADDY_RESTART_CONFIRMATION_REQUIRED", "caddy_restart_confirmation_required"),
    )
    cert_renewal_monitor_timeout_seconds: float = Field(
        default=180.0,
        gt=0,
        validation_alias=AliasChoices("CB_CERT_RENEWAL_MONITOR_TIMEOUT_SECONDS", "cert_renewal_monitor_timeout_seconds"),
    )
    cert_renewal_poll_interval_seconds: float = Field(
        default=2.0,
        gt=0,
        validation_alias=AliasChoices("CB_CERT_RENEWAL_POLL_INTERVAL_SECONDS", "cert_renewal_poll_interval_seconds"),
    )
    caddy_api_url: str = Field(
        default=DEFAULT_CADDY_ADMIN_URL,
        validation_alias="CB_CADDY_API_URL",
    )
    caddy_api_port: int = Field(
        default=2019,
        ge=1,
        le=65535,
        validation_alias=AliasChoices(
            "CADDYBUDDY_CADDY_API_PORT",
            "CADDY_API_PORT",
            "caddy_api_port",
        ),
    )
    caddy_admin_api_path: str = Field(
        default="/load",
        validation_alias=AliasChoices(
            "CADDYBUDDY_CADDY_ADMIN_API_PATH",
            "CADDY_ADMIN_API_PATH",
            "caddy_admin_api_path",
        ),
    )
    caddy_admin_url: str = Field(default=DEFAULT_CADDY_ADMIN_URL)
    caddy_admin_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        le=60.0,
        validation_alias=AliasChoices(
            "CADDYBUDDY_CADDY_ADMIN_TIMEOUT_SECONDS",
            "CADDY_ADMIN_TIMEOUT_SECONDS",
            "caddy_admin_timeout_seconds",
        ),
    )
    caddy_startup_reconcile_timeout_seconds: float = Field(
        default=120.0,
        gt=0,
        le=900.0,
        validation_alias=AliasChoices(
            "CADDYBUDDY_CADDY_STARTUP_RECONCILE_TIMEOUT_SECONDS",
            "CADDY_STARTUP_RECONCILE_TIMEOUT_SECONDS",
            "caddy_startup_reconcile_timeout_seconds",
        ),
    )
    auto_onboard: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "CADDYBUDDY_AUTO_ONBOARD",
            "AUTO_ONBOARD",
            "auto_onboard",
        ),
    )
    caddy_baseline_caddyfile: str = Field(
        default="""\
{
    log {
        level info
        format json
        output stdout
    }

    # Only enable when Caddy runs behind a trusted proxy/CDN.
    #
    # servers {
    #     trusted_proxies static private_ranges
    #     trusted_proxies_strict
    # }
}

(security_headers) {
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        # X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"

        -Server
        -X-Powered-By
    }
}

(security_headers_strict) {
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "DENY"
        Referrer-Policy "strict-origin-when-cross-origin"

        -Server
        -X-Powered-By
    }
}

(default_log) {
    log {
        output stdout
        format json
    }
}""",
        validation_alias=AliasChoices(
            "CADDYBUDDY_CADDY_BASELINE",
            "CADDY_BASELINE",
            "caddy_baseline_caddyfile",
        ),
    )
    mounted_caddyfile_path: Path | None = Field(default=DEFAULT_CADDYFILE_PATH)
    caddy_certificates_path: Path | None = Field(
        default=Path("/var/lib/caddy/.local/share/caddy/certificates"),
        validation_alias=AliasChoices(
            "CB_CADDY_CERTIFICATES_PATH",
            "CADDY_CERTIFICATES_PATH",
            "caddy_certificates_path",
        ),
    )
    ssllabs_api_base_url: str = Field(
        default="https://api.ssllabs.com/api/v4",
        validation_alias=AliasChoices(
            "CADDYBUDDY_SSLLABS_API_BASE_URL",
            "SSLLABS_API_BASE_URL",
            "ssllabs_api_base_url",
        ),
    )
    ssllabs_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        le=60.0,
        validation_alias=AliasChoices(
            "CADDYBUDDY_SSLLABS_TIMEOUT_SECONDS",
            "SSLLABS_TIMEOUT_SECONDS",
            "ssllabs_timeout_seconds",
        ),
    )
    ssllabs_cache_max_age_hours: int = Field(
        default=24,
        ge=1,
        le=168,
        validation_alias=AliasChoices(
            "CADDYBUDDY_SSLLABS_CACHE_MAX_AGE_HOURS",
            "SSLLABS_CACHE_MAX_AGE_HOURS",
            "ssllabs_cache_max_age_hours",
        ),
    )

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

    @field_validator("default_admin_email", mode="before")
    @classmethod
    def _validate_default_admin_email(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        if not normalized:
            raise ValueError("Default admin email must not be empty.")
        if not re.fullmatch(_SIMPLE_EMAIL_PATTERN, normalized):
            raise ValueError("Default admin email must be a valid email address.")
        return normalized

    @field_validator("session_cookie_name", mode="before")
    @classmethod
    def _validate_session_cookie_name(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        if not normalized:
            raise ValueError("session_cookie_name must not be empty.")
        if _SESSION_COOKIE_NAME_PATTERN.fullmatch(normalized) is None:
            raise ValueError(
                "session_cookie_name may contain only letters, numbers, dots, underscores, and hyphens."
            )
        return normalized

    @field_validator("caddy_api_url", "caddy_admin_url", mode="before")
    @classmethod
    def _normalize_caddy_http_url(cls, value: object, info) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{info.field_name} must not be empty.")

        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(f"{info.field_name} must use http or https.")
        if parsed.username or parsed.password:
            raise ValueError(f"{info.field_name} must not include username or password.")
        if not parsed.hostname:
            raise ValueError(f"{info.field_name} must include a host.")
        if parsed.path not in {"", "/"}:
            raise ValueError(f"{info.field_name} must not include a path.")
        if parsed.query or parsed.fragment:
            raise ValueError(f"{info.field_name} must not include query or fragment.")

        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"{info.field_name} has an invalid port.") from exc

        host = parsed.hostname
        if host is None:
            raise ValueError(f"{info.field_name} must include a host.")
        if ":" in host:
            host = f"[{host}]"
        netloc = f"{host}:{port}" if port is not None else host
        return urlunsplit((parsed.scheme, netloc, "", "", ""))

    @field_validator("ssllabs_api_base_url", mode="before")
    @classmethod
    def _normalize_ssllabs_api_base_url(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = value.strip()
        if not normalized:
            raise ValueError("ssllabs_api_base_url must not be empty.")

        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("ssllabs_api_base_url must use http or https.")
        if parsed.username or parsed.password:
            raise ValueError("ssllabs_api_base_url must not include username or password.")
        if not parsed.hostname:
            raise ValueError("ssllabs_api_base_url must include a host.")
        if parsed.query or parsed.fragment:
            raise ValueError("ssllabs_api_base_url must not include query or fragment.")

        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("ssllabs_api_base_url has an invalid port.") from exc

        host = parsed.hostname
        if host is None:
            raise ValueError("ssllabs_api_base_url must include a host.")
        normalized_host = host.lower()
        if parsed.scheme == "http" and normalized_host not in _LOOPBACK_HOSTS:
            raise ValueError("ssllabs_api_base_url must use https unless it targets localhost.")
        if ":" in host:
            host = f"[{host}]"

        path = "/" + parsed.path.strip().strip("/") if parsed.path else ""
        netloc = f"{host}:{port}" if port is not None else host
        return urlunsplit((parsed.scheme, netloc, path, "", ""))

    @field_validator("caddy_admin_api_path", mode="before")
    @classmethod
    def _normalize_caddy_admin_api_path(cls, value: object) -> object:
        if not isinstance(value, str):
            return value

        normalized = "/" + value.strip().strip("/")
        if not normalized:
            raise ValueError("Caddy admin API path must not be empty.")
        if "?" in normalized or "#" in normalized:
            raise ValueError("Caddy admin API path must not contain query or fragment.")
        if normalized != "/load":
            raise ValueError("Only the Caddy /load endpoint is supported.")
        return normalized

    @field_validator("mounted_caddyfile_path", "caddy_certificates_path", mode="before")
    @classmethod
    def _normalize_optional_path(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            return Path(normalized)
        return value

    @field_validator("mounted_caddyfile_path")
    @classmethod
    def _validate_mounted_caddyfile_path(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        if not value.is_absolute():
            raise ValueError("mounted_caddyfile_path must be absolute.")
        if value.name != "Caddyfile":
            raise ValueError("mounted_caddyfile_path must point to a file named 'Caddyfile'.")
        return value

    @field_validator("forwarded_allow_ips", mode="before")
    @classmethod
    def _validate_forwarded_allow_ips(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        entries = [part.strip() for part in value.split(",") if part.strip()]
        if not entries:
            raise ValueError("forwarded_allow_ips must not be empty.")
        if "*" in entries:
            logging.getLogger(__name__).warning(
                "FORWARDED_ALLOW_IPS='*' trusts all proxy IPs. "
                "Set explicit trusted proxy IPs in production."
            )
        return ",".join(entries)

    @model_validator(mode="after")
    def _validate_non_development_secrets(self) -> Self:
        secret_key = self.secret_key.get_secret_value().strip()
        if secret_key in _INSECURE_SECRET_VALUES or len(secret_key) < _MIN_SECRET_LENGTH:
            raise ValueError(
                f"Set a strong secret key of at least {_MIN_SECRET_LENGTH} characters via "
                "CB_SECRET_KEY, CADDYBUDDY_SECRET_KEY, or SECRET_KEY."
            )

        if self.password_pepper is not None:
            password_pepper = self.password_pepper.get_secret_value().strip()
            if password_pepper in _INSECURE_SECRET_VALUES or len(password_pepper) < _MIN_SECRET_LENGTH:
                raise ValueError(
                    f"Set a strong password pepper of at least {_MIN_SECRET_LENGTH} characters via "
                    "CADDYBUDDY_PASSWORD_PEPPER or PASSWORD_PEPPER."
                )

        if self.session_cookie_samesite == "none" and not self.session_https_only:
            raise ValueError("session_cookie_samesite='none' requires session_https_only=true.")

        return self

    @property
    def base_dir(self) -> Path:
        return _BASE_DIR

    @property
    def data_dir(self) -> Path:
        return _DATA_DIR

    @property
    def caddyfile_path(self) -> Path | None:
        return self.mounted_caddyfile_path


@cache
def get_settings() -> Settings:
    return Settings()

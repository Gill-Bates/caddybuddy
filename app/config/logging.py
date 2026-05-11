#!/usr/bin/env python3
#
# app/config/logging.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import logging
import re
from copy import copy, deepcopy

from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter


_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_DARK_GRAY = "\033[90m"
_RESET = "\033[0m"
_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_SENSITIVE_QUERY_PARAM_PATTERN = re.compile(
    r"((?:token|api_key|password|secret|access_token)=)[^&\s]*",
    re.IGNORECASE,
)
_NOISY_DEBUG_LOGGERS = (
    "aiosqlite",
    "httpcore",
    "httpx",
    "multipart",
    "python_multipart",
    "sqlalchemy.engine",
    "sqlalchemy.pool",
)


def _redact_sensitive_query_params(value: str) -> str:
    return _SENSITIVE_QUERY_PARAM_PATTERN.sub(r"\1***REDACTED***", value)


class SuppressVerboseDependencyFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.INFO:
            return True
        return not any(
            record.name == logger_name or record.name.startswith(f"{logger_name}.")
            for logger_name in _NOISY_DEBUG_LOGGERS
        )


class _TimestampPrefixMixin:
    def _format_timestamp(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, self.datefmt)
        if getattr(self, "use_colors", False):
            return f"{_DARK_GRAY}{timestamp}{_RESET}"
        return timestamp

    def formatMessage(self, record: logging.LogRecord) -> str:
        record_copy = copy(record)
        request_line = getattr(record_copy, "request_line", None)
        if isinstance(request_line, str):
            record_copy.request_line = _redact_sensitive_query_params(request_line)
        record_copy.__dict__["timestamp"] = self._format_timestamp(record_copy)
        return super().formatMessage(record_copy)


class TimestampDefaultFormatter(_TimestampPrefixMixin, DefaultFormatter):
    pass


class TimestampAccessFormatter(_TimestampPrefixMixin, AccessFormatter):
    def formatMessage(self, record: logging.LogRecord) -> str:
        record_copy = copy(record)
        args = getattr(record_copy, "args", ())
        if isinstance(args, tuple) and len(args) == 5:
            client_addr, method, full_path, http_version, status_code = args
            if isinstance(full_path, str):
                record_copy.args = (
                    client_addr,
                    method,
                    _redact_sensitive_query_params(full_path),
                    http_version,
                    status_code,
                )
        return super().formatMessage(record_copy)


def build_log_config(level_name: str) -> dict[str, object]:
    normalized_level = level_name.strip().upper()
    if normalized_level not in _VALID_LOG_LEVELS:
        raise ValueError(f"Invalid log level: {level_name}")

    log_config = deepcopy(LOGGING_CONFIG)
    log_config["filters"] = {
        "suppress_verbose_dependencies": {
            "()": "app.config.logging.SuppressVerboseDependencyFilter",
        }
    }
    log_config["formatters"]["default"] = {
        "()": "app.config.logging.TimestampDefaultFormatter",
        "fmt": "%(timestamp)s %(levelprefix)s %(message)s",
        "datefmt": _TIMESTAMP_FORMAT,
        "use_colors": None,
    }
    log_config["formatters"]["access"] = {
        "()": "app.config.logging.TimestampAccessFormatter",
        "fmt": '%(timestamp)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        "datefmt": _TIMESTAMP_FORMAT,
        "use_colors": None,
    }
    log_config["root"] = {
        "handlers": ["default"],
        "level": normalized_level,
    }
    # Set handler levels explicitly to allow DEBUG messages through
    log_config["handlers"]["default"]["level"] = normalized_level
    log_config["handlers"]["default"]["filters"] = ["suppress_verbose_dependencies"]
    log_config["handlers"]["access"]["level"] = normalized_level
    log_config["loggers"]["uvicorn"]["level"] = normalized_level
    log_config["loggers"]["uvicorn.access"]["level"] = normalized_level
    log_config["loggers"]["uvicorn.error"]["level"] = normalized_level
    log_config["loggers"]["aiosqlite"] = {
        "handlers": ["default"],
        "level": "WARNING",
        "propagate": False,
    }
    log_config["loggers"]["httpcore"] = {
        "handlers": ["default"],
        "level": "WARNING",
        "propagate": False,
    }
    log_config["loggers"]["httpx"] = {
        "handlers": ["default"],
        "level": "WARNING",
        "propagate": False,
    }
    log_config["loggers"]["sqlalchemy.engine"] = {
        "handlers": ["default"],
        "level": "WARNING",
        "propagate": False,
    }
    log_config["loggers"]["sqlalchemy.pool"] = {
        "handlers": ["default"],
        "level": "WARNING",
        "propagate": False,
    }
    log_config["loggers"]["multipart"] = {
        "handlers": ["default"],
        "level": "WARNING",
        "propagate": False,
    }
    log_config["loggers"]["python_multipart"] = {
        "handlers": ["default"],
        "level": "WARNING",
        "propagate": False,
    }
    return log_config
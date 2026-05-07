#!/usr/bin/env python3
#
# app/config/logging.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from __future__ import annotations

import logging
from copy import copy, deepcopy

from uvicorn.config import LOGGING_CONFIG
from uvicorn.logging import AccessFormatter, DefaultFormatter


_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_DARK_GRAY = "\033[90m"
_RESET = "\033[0m"
_NOISY_DEBUG_LOGGERS = (
    "aiosqlite",
    "sqlalchemy.engine",
    "sqlalchemy.pool",
)


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
        record_copy.__dict__["timestamp"] = self._format_timestamp(record_copy)
        return super().formatMessage(record_copy)


class TimestampDefaultFormatter(_TimestampPrefixMixin, DefaultFormatter):
    pass


class TimestampAccessFormatter(_TimestampPrefixMixin, AccessFormatter):
    pass


def build_log_config(level_name: str) -> dict[str, object]:
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
        "level": level_name,
    }
    # Set handler levels explicitly to allow DEBUG messages through
    log_config["handlers"]["default"]["level"] = level_name
    log_config["handlers"]["default"]["filters"] = ["suppress_verbose_dependencies"]
    log_config["handlers"]["access"]["level"] = level_name
    log_config["loggers"]["uvicorn"]["level"] = level_name
    log_config["loggers"]["uvicorn.access"]["level"] = level_name
    log_config["loggers"]["uvicorn.error"]["level"] = level_name
    log_config["loggers"]["aiosqlite"] = {
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
    return log_config
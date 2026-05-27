#!/usr/bin/env python3
#
# app/schemas/__init__.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""Pydantic schemas."""

from app.schemas.system import BuildInfoResponse, HealthResponse

__all__ = [
    # System
    "BuildInfoResponse",
    "HealthResponse",
]

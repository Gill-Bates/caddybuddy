#!/usr/bin/env python3
#
# app/schemas/system.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    app: str
    version: str


class BuildInfoResponse(BaseModel):
    version: str
    commit: str
#!/usr/bin/env python3
#
# app/routers/api.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

from fastapi import APIRouter

from app.schemas.system import BuildInfoResponse, HealthResponse
from app.services.build_info import get_build_info


router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def healthcheck() -> HealthResponse:
    build_info = get_build_info()
    return HealthResponse(status="ok", app="CaddyBuddy", version=build_info["version"])


@router.get("/build-info", response_model=BuildInfoResponse)
async def build_info() -> BuildInfoResponse:
    return BuildInfoResponse(**get_build_info())
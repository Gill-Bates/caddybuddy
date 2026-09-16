#!/usr/bin/env python3
#
# app/schemas/passkeys.py
# Copyright (C) 2026 Gill-Bates http://github.com/Gill-Bates
#

"""API contract for the WebAuthn (passkey) ceremony endpoints."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.services.auth import PASSWORD_MAX_LENGTH

# An attestation object carrying a full certificate chain is the largest
# realistic payload; anything beyond this is rejected before it reaches the
# WebAuthn parser.
MAX_CREDENTIAL_PAYLOAD_BYTES = 16384
MAX_DEVICE_NAME_LENGTH = 100


def _validate_credential_payload(value: dict[str, Any]) -> dict[str, Any]:
    if not value:
        raise ValueError("credential must not be empty")
    if len(json.dumps(value, separators=(",", ":"))) > MAX_CREDENTIAL_PAYLOAD_BYTES:
        raise ValueError("credential payload is too large")
    return value


class PasskeyRegistrationStartRequest(BaseModel):
    """Current-password confirmation for passkey registration."""

    current_password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)


class PasskeyRegistrationFinishRequest(BaseModel):
    """Registration response produced by ``navigator.credentials.create()``."""

    credential: dict[str, Any]
    device_name: str | None = Field(default=None, max_length=MAX_DEVICE_NAME_LENGTH)

    @field_validator("credential")
    @classmethod
    def _check_credential(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_credential_payload(value)


class PasskeyAuthenticationFinishRequest(BaseModel):
    """Assertion produced by ``navigator.credentials.get()``."""

    credential: dict[str, Any]
    next_url: str | None = Field(default=None, max_length=512)

    @field_validator("credential")
    @classmethod
    def _check_credential(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_credential_payload(value)


class PasskeyOptionsResponse(BaseModel):
    """WebAuthn ceremony options in standard WebAuthn JSON form."""

    options: dict[str, Any]


class PasskeyMutationResponse(BaseModel):
    success: bool
    message: str


class PasskeyRegistrationFinishResponse(PasskeyMutationResponse):
    pass


class PasskeyLoginFinishResponse(PasskeyMutationResponse):
    redirect_url: str

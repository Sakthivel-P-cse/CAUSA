"""Fail-closed bearer-token authentication and small role hierarchy."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

ROLE_LEVEL = {"viewer": 10, "proposer": 20, "approver": 30, "executor": 40}


def _truthy(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def auth_required() -> bool:
    return _truthy("CAUSA_API_REQUIRE_AUTH", "1")


def configured_tokens() -> dict[str, str]:
    pairs = (
        ("CAUSA_API_VIEWER_TOKEN", "viewer"),
        ("CAUSA_API_TOKEN", "viewer"),
        ("CAUSA_REMEDIATION_PROPOSER_TOKEN", "proposer"),
        ("REMEDIATION_APPROVAL_TOKEN", "approver"),
        ("CAUSA_REMEDIATION_EXECUTOR_TOKEN", "executor"),
    )
    tokens: dict[str, str] = {}
    for name, role in pairs:
        value = os.getenv(name, "").strip()
        if value:
            tokens[value] = role
    return tokens


def validate_auth_configuration() -> None:
    if not auth_required():
        return
    if not configured_tokens():
        raise RuntimeError(
            "CAUSA_API_REQUIRE_AUTH=1 but no API bearer tokens are configured"
        )
    if not os.getenv("REMEDIATION_APPROVAL_TOKEN", "").strip():
        raise RuntimeError(
            "CAUSA_API_REQUIRE_AUTH=1 but REMEDIATION_APPROVAL_TOKEN is absent"
        )


def _bearer(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, separator, value = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer":
        return ""
    return value.strip()


@dataclass(frozen=True)
class AuthIdentity:
    role: str
    subject: str


class AuthenticationError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.detail = {"code": code, "message": message}
        self.headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else {}


def authenticate(authorization: str | None) -> AuthIdentity:
    if not auth_required():
        return AuthIdentity(role="executor", subject="development-auth-disabled")

    supplied = _bearer(authorization)
    if not supplied:
        raise AuthenticationError(
            401,
            "auth_missing",
            "Bearer token required",
        )
    for configured, role in configured_tokens().items():
        if hmac.compare_digest(supplied, configured):
            return AuthIdentity(role=role, subject=f"token:{role}")
    raise AuthenticationError(
        401,
        "auth_invalid",
        "Invalid bearer token",
    )


def require_role(required: str):
    from fastapi import Header, HTTPException

    if required not in ROLE_LEVEL:
        raise ValueError(f"unknown role {required}")

    def dependency(authorization: str | None = Header(default=None)) -> AuthIdentity:
        try:
            identity = authenticate(authorization)
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=exc.headers,
            ) from exc
        if ROLE_LEVEL[identity.role] < ROLE_LEVEL[required]:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "role_denied",
                    "message": f"{required} role required",
                },
            )
        return identity

    return dependency

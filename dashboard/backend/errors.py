from __future__ import annotations

import logging
from typing import NoReturn

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)


def api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def raise_upstream_error(
    service: str,
    operation: str,
    exc: Exception,
    *,
    not_found: bool = False,
) -> NoReturn:
    logger.exception("%s %s failed", service, operation)
    if isinstance(exc, httpx.TimeoutException):
        raise api_error(504, f"{service}_timeout", f"{service.title()} request timed out")
    if isinstance(exc, httpx.HTTPStatusError):
        if not_found and exc.response.status_code == 404:
            raise api_error(404, "not_found", "Requested resource was not found")
        raise api_error(
            502, f"{service}_bad_response", f"{service.title()} returned an invalid response"
        )
    if isinstance(exc, httpx.RequestError):
        raise api_error(503, f"{service}_unavailable", f"{service.title()} is unavailable")
    raise api_error(
        502, f"{service}_bad_response", f"{service.title()} returned an invalid response"
    )


def raise_database_error(operation: str, exc: Exception) -> NoReturn:
    logger.exception("postgres %s failed", operation)
    raise api_error(503, "database_unavailable", "Dashboard data is temporarily unavailable")

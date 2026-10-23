from __future__ import annotations

import json
from typing import Any


class GatewayError(Exception):
    """Every failure that leaves the gateway is expressed with this type."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 500,
        error_type: str = "internal_error",
        code: str = "internal_error",
        param: str | None = None,
        retryable: bool = False,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_type = error_type
        self.code = code
        self.param = param
        self.retryable = retryable
        self.details = details
        self.headers = headers or {}

    def to_dict(self, request_id: str | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {
            "message": self.message,
            "type": self.error_type,
            "code": self.code,
            "param": self.param,
            "request_id": request_id,
        }
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}


class UpstreamError(GatewayError):
    """Upstream-specific error; retryable controls exponential backoff."""


def _upstream_message(body_text: str) -> tuple[str | None, Any]:
    try:
        payload = json.loads(body_text)
    except (json.JSONDecodeError, TypeError):
        return None, body_text[:1000]
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return error.get("message"), payload
        if isinstance(error, str):
            return error, payload
    return None, payload


def map_upstream_http_error(
    model: str,
    status_code: int,
    body_text: str,
    retry_statuses: set[int],
    retry_after: str | None = None,
) -> UpstreamError:
    """Translate an upstream HTTP failure into the gateway's unified error codes."""

    message, details = _upstream_message(body_text)
    headers = {"Retry-After": retry_after} if retry_after else {}
    base_message = message or f"Upstream {model} returned HTTP {status_code}"

    if status_code == 429:
        return UpstreamError(
            base_message,
            status_code=429,
            error_type="rate_limit_error",
            code="upstream_rate_limit",
            retryable=status_code in retry_statuses,
            details=details,
            headers=headers,
        )
    if status_code in (401, 403):
        return UpstreamError(
            base_message,
            status_code=502,
            error_type="upstream_error",
            code="upstream_auth_error",
            retryable=False,
            details=details,
        )
    if status_code in (400, 422):
        return UpstreamError(
            base_message,
            status_code=422,
            error_type="invalid_request_error",
            code="upstream_rejected",
            retryable=False,
            details=details,
        )
    if status_code == 408:
        return UpstreamError(
            base_message,
            status_code=504,
            error_type="upstream_error",
            code="upstream_timeout",
            retryable=True,
            details=details,
        )
    return UpstreamError(
        base_message,
        status_code=502,
        error_type="upstream_error",
        code="upstream_error",
        retryable=status_code in retry_statuses,
        details=details,
        headers=headers,
    )

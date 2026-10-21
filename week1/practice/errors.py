"""失败协议 — 把底层异常归一化为统一的 ModelCallError。

run.failed 事件里的 code / retryable 就来自这里：
订阅方（客户端 / 审计系统）不需要理解 openai、httpx 的异常体系，
只需要认这套失败码。与 1-2 笔记 normalize_exception.py 同款设计。
"""

from dataclasses import dataclass
from typing import Literal

# openai 是可选依赖：MODEL_PROVIDER=fake 离线模式下不需要安装。
try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        InternalServerError,
        RateLimitError,
    )

    _OPENAI_AVAILABLE = True
except ModuleNotFoundError:
    _OPENAI_AVAILABLE = False

ErrorCode = Literal[
    "authentication",       # API Key 无效/过期，不可重试
    "invalid_request",      # 请求参数错误，不可重试
    "rate_limited",         # 被限流，可重试
    "overloaded",           # 服务端 503，可重试
    "timeout",              # 超时，可重试
    "network",              # 网络错误，可重试
    "model_stream_failed",  # 流中途失败，通常可重试
    "output_truncated",     # finish_reason=length，输出被截断
    "unknown",
]


@dataclass
class ModelCallError(Exception):
    code: ErrorCode
    message: str
    retryable: bool
    status_code: int | None = None
    request_id: str | None = None


def normalize_exception(exc: Exception) -> ModelCallError:
    """任何异常 → 统一失败协议。已经归一化的直接透传。"""
    if isinstance(exc, ModelCallError):
        return exc
    if _OPENAI_AVAILABLE:
        if isinstance(exc, AuthenticationError):
            return ModelCallError("authentication", str(exc), False, 401)
        if isinstance(exc, BadRequestError):
            return ModelCallError(
                "invalid_request",
                str(exc),
                False,
                getattr(exc, "status_code", None),
            )
        if isinstance(exc, RateLimitError):
            return ModelCallError("rate_limited", str(exc), True, 429)
        if isinstance(exc, APITimeoutError):
            return ModelCallError("timeout", str(exc), True)
        if isinstance(exc, APIConnectionError):
            return ModelCallError("network", str(exc), True)
        if isinstance(exc, InternalServerError):
            status = getattr(exc, "status_code", None)
            code: ErrorCode = "overloaded" if status == 503 else "model_stream_failed"
            return ModelCallError(code, str(exc), True, status)
    return ModelCallError("model_stream_failed", str(exc), True)

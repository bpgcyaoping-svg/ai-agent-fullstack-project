from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.config import ModelConfig
from app.core.errors import GatewayError
from app.schemas import ResponseFormat


@dataclass
class AdapterRequest:
    """Protocol-neutral request handed to every adapter."""

    upstream_model: str
    system: str | None
    messages: list[dict[str, str]]
    temperature: float | None = None
    max_tokens: int = 2048
    response_format: ResponseFormat | None = None
    stream: bool = False


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "reasoning_tokens": self.reasoning_tokens,
        }


@dataclass
class AdapterResult:
    text: str
    finish_reason: str = "stop"
    usage: TokenUsage = field(default_factory=TokenUsage)
    upstream_model: str | None = None


@dataclass
class StreamEvent:
    text: str = ""
    finish_reason: str | None = None
    usage: TokenUsage | None = None
    done: bool = False


@dataclass
class PreparedRequest:
    url: str
    headers: dict[str, str]
    payload: dict[str, Any]


class BaseAdapter(ABC):
    """Encapsulates one upstream protocol: auth, request body, response and SSE shapes."""

    def __init__(self, model: ModelConfig) -> None:
        self.model = model

    def api_key(self) -> str:
        key = os.environ.get(self.model.api_key_env, "")
        if not key:
            raise GatewayError(
                f"Missing upstream credential: environment variable {self.model.api_key_env} is empty",
                status_code=500,
                error_type="internal_error",
                code="gateway_misconfigured",
                param=self.model.api_key_env,
            )
        return key

    def _base_headers(self, request_id: str, *, stream: bool = False) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "X-Request-ID": request_id,
        }

    @abstractmethod
    def prepare(self, request: AdapterRequest, request_id: str) -> PreparedRequest:
        """Build the protocol-specific URL, headers and JSON body."""

    @abstractmethod
    def parse_response(self, payload: dict[str, Any]) -> AdapterResult:
        """Normalize a non-streaming upstream payload."""

    @abstractmethod
    def stream_parser(self) -> Any:
        """Return a stateful parser with feed(raw)->list[StreamEvent] and finish()."""

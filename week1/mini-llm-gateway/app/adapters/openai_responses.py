from __future__ import annotations

import json
from typing import Any

from app.adapters.base import (
    AdapterRequest,
    AdapterResult,
    BaseAdapter,
    PreparedRequest,
    StreamEvent,
    TokenUsage,
)
from app.core.errors import UpstreamError
from app.schemas import ResponseFormat


def _text_format(response_format: ResponseFormat | None) -> dict[str, Any] | None:
    if response_format is None:
        return None
    if response_format.type == "json_schema":
        spec = response_format.json_schema
        if spec is None:
            return None
        fmt: dict[str, Any] = {"type": "json_schema", "schema": spec.schema_}
        if spec.name:
            fmt["name"] = spec.name
        if spec.strict is not None:
            fmt["strict"] = spec.strict
        return fmt
    if response_format.type == "json_object":
        return {"type": "json_object"}
    return None


def _usage_from(payload: dict[str, Any]) -> TokenUsage:
    raw = payload.get("usage") or {}
    input_details = raw.get("input_tokens_details") or {}
    output_details = raw.get("output_tokens_details") or {}
    return TokenUsage(
        prompt_tokens=int(raw.get("input_tokens") or 0),
        completion_tokens=int(raw.get("output_tokens") or 0),
        cached_tokens=int(input_details.get("cached_tokens") or 0),
        reasoning_tokens=int(output_details.get("reasoning_tokens") or 0),
    )


class OpenAIResponsesAdapter(BaseAdapter):
    """Adapter for the OpenAI Responses API (POST /v1/responses)."""

    def prepare(self, request: AdapterRequest, request_id: str) -> PreparedRequest:
        payload: dict[str, Any] = {
            "model": request.upstream_model,
            "input": [
                {"role": message["role"], "content": message["content"]}
                for message in request.messages
            ],
            "max_output_tokens": request.max_tokens,
        }
        if request.system:
            payload["instructions"] = request.system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.stream:
            payload["stream"] = True

        text_format = _text_format(request.response_format)
        if text_format is not None:
            payload["text"] = {"format": text_format}

        headers = self._base_headers(request_id, stream=request.stream)
        headers["Authorization"] = f"Bearer {self.api_key()}"
        return PreparedRequest(url=self.model.url, headers=headers, payload=payload)

    def parse_response(self, payload: dict[str, Any]) -> AdapterResult:
        parts: list[str] = []
        for item in payload.get("output") or []:
            if item.get("type") != "message":
                continue
            for block in item.get("content") or []:
                if block.get("type") in ("output_text", "text") and block.get("text"):
                    parts.append(block["text"])
        text = "".join(parts) or (payload.get("output_text") or "")
        finish_reason = "length" if payload.get("status") == "incomplete" else "stop"
        return AdapterResult(
            text=text,
            finish_reason=finish_reason,
            usage=_usage_from(payload),
            upstream_model=payload.get("model"),
        )

    def stream_parser(self) -> _ResponsesStreamParser:
        return _ResponsesStreamParser()


class _ResponsesStreamParser:
    """Turns Responses API SSE events into protocol-neutral StreamEvents."""

    def __init__(self) -> None:
        self.done = False
        self.usage: TokenUsage | None = None
        self.finish_reason: str | None = None

    def feed(self, raw: str) -> list[StreamEvent]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []

        event_type = data.get("type")
        if event_type == "response.output_text.delta":
            piece = data.get("delta") or ""
            return [StreamEvent(text=piece)] if piece else []

        if event_type in ("response.completed", "response.incomplete"):
            response = data.get("response") or data
            self.usage = _usage_from(response)
            self.finish_reason = "stop" if event_type == "response.completed" else "length"
            self.done = True
            return [StreamEvent(usage=self.usage, finish_reason=self.finish_reason, done=True)]

        if event_type in ("response.failed", "error"):
            message = (
                data.get("message")
                or (data.get("response") or {}).get("error", {}).get("message")
                or "Upstream Responses stream failed"
            )
            raise UpstreamError(
                str(message),
                status_code=502,
                error_type="upstream_error",
                code="upstream_error",
            )
        return []

    def finish(self) -> StreamEvent:
        return StreamEvent(
            usage=self.usage, finish_reason=self.finish_reason or "stop", done=True
        )

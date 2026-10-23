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

STRUCTURED_TOOL_NAME = "structured_output"


def _usage_from(payload: dict[str, Any]) -> TokenUsage:
    raw = payload.get("usage") or {}
    cache_read = int(raw.get("cache_read_input_tokens") or 0)
    cache_creation = int(raw.get("cache_creation_input_tokens") or 0)
    return TokenUsage(
        prompt_tokens=int(raw.get("input_tokens") or 0) + cache_read + cache_creation,
        completion_tokens=int(raw.get("output_tokens") or 0),
        cached_tokens=cache_read,
    )


_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "tool_use": "tool_use",
    "max_tokens": "length",
}


class AnthropicMessagesAdapter(BaseAdapter):
    """Adapter for the Anthropic Messages API (POST /v1/messages).

    The Messages API has no response_format field, so JSON schema enforcement is
    implemented with a single forced tool call.
    """

    def prepare(self, request: AdapterRequest, request_id: str) -> PreparedRequest:
        payload: dict[str, Any] = {
            "model": request.upstream_model,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": message["role"], "content": message["content"]}
                for message in request.messages
            ],
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.stream:
            payload["stream"] = True

        system = request.system
        fmt: ResponseFormat | None = request.response_format
        if fmt is not None and fmt.type == "json_object":
            instruction = "Return only a single valid JSON object with no surrounding prose."
            system = f"{system}\n\n{instruction}" if system else instruction
        elif fmt is not None and fmt.type == "json_schema":
            spec = fmt.json_schema
            payload["tools"] = [
                {
                    "name": STRUCTURED_TOOL_NAME,
                    "description": "Return the answer as a JSON object matching the schema.",
                    "input_schema": spec.schema_ if spec else {"type": "object"},
                }
            ]
            payload["tool_choice"] = {"type": "tool", "name": STRUCTURED_TOOL_NAME}
            # DeepSeek thinking mode does not support forced tool use.
            payload["thinking"] = {"type": "disabled"}

        if system:
            payload["system"] = system

        headers = self._base_headers(request_id, stream=request.stream)
        headers["x-api-key"] = self.api_key()
        headers["anthropic-version"] = self.model.anthropic_version
        return PreparedRequest(url=self.model.url, headers=headers, payload=payload)

    def parse_response(self, payload: dict[str, Any]) -> AdapterResult:
        parts: list[str] = []
        tool_input: Any = None
        for block in payload.get("content") or []:
            block_type = block.get("type")
            if block_type == "text":
                parts.append(block.get("text") or "")
            elif block_type == "tool_use":
                tool_input = block.get("input")

        text = (
            json.dumps(tool_input, ensure_ascii=False)
            if tool_input is not None
            else "".join(parts)
        )
        raw_reason = payload.get("stop_reason") or "end_turn"
        return AdapterResult(
            text=text,
            finish_reason=_STOP_REASONS.get(raw_reason, "stop"),
            usage=_usage_from(payload),
            upstream_model=payload.get("model"),
        )

    def stream_parser(self) -> _AnthropicStreamParser:
        return _AnthropicStreamParser()


class _AnthropicStreamParser:
    """Turns Anthropic Messages SSE events into protocol-neutral StreamEvents."""

    def __init__(self) -> None:
        self.done = False
        self.usage: TokenUsage | None = None
        self.finish_reason: str | None = None
        self._tool_json: dict[int, list[str]] = {}

    def feed(self, raw: str) -> list[StreamEvent]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []

        event_type = data.get("type")
        if event_type == "message_start":
            message = data.get("message") or {}
            self.usage = _usage_from(message)
            return []

        if event_type == "content_block_delta":
            delta = data.get("delta") or {}
            if delta.get("type") == "text_delta":
                piece = delta.get("text") or ""
                return [StreamEvent(text=piece)] if piece else []
            if delta.get("type") == "input_json_delta":
                index = int(data.get("index") or 0)
                self._tool_json.setdefault(index, []).append(delta.get("partial_json") or "")
            return []

        if event_type == "content_block_stop":
            index = int(data.get("index") or 0)
            pieces = self._tool_json.pop(index, None)
            if pieces:
                return [StreamEvent(text="".join(pieces))]
            return []

        if event_type == "message_delta":
            usage = data.get("usage") or {}
            if self.usage is None:
                self.usage = TokenUsage()
            if "output_tokens" in usage:
                self.usage.completion_tokens = int(usage["output_tokens"] or 0)
            stop_reason = (data.get("delta") or {}).get("stop_reason")
            if stop_reason:
                self.finish_reason = _STOP_REASONS.get(stop_reason, "stop")
            return []

        if event_type == "message_stop":
            self.done = True
            return [
                StreamEvent(
                    usage=self.usage,
                    finish_reason=self.finish_reason or "stop",
                    done=True,
                )
            ]

        if event_type == "error":
            error = data.get("error") or {}
            raise UpstreamError(
                error.get("message") or "Upstream Anthropic stream failed",
                status_code=502,
                error_type="upstream_error",
                code="upstream_error",
            )
        return []

    def finish(self) -> StreamEvent:
        return StreamEvent(
            usage=self.usage, finish_reason=self.finish_reason or "stop", done=True
        )

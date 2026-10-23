from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Callable

import httpx
import pytest

from app.config import GatewayConfig
from app.main import create_app


def make_config(tmp_path, **overrides) -> GatewayConfig:
    raw = {
        "database_path": str(tmp_path / "gateway.db"),
        "retry": {
            "max_attempts": 3,
            "base_delay_seconds": 0.01,
            "max_delay_seconds": 0.02,
        },
        "structured_repair_attempts": 1,
        "models": {
            "pro": {
                "protocol": "openai_responses",
                "base_url": "https://pro.test",
                "path": "/v1/responses",
                "upstream_model": "upstream-pro",
                "api_key_env": "TEST_UPSTREAM_KEY",
                "rate_limit": {"requests_per_minute": 60, "burst": 10},
            },
            "flash": {
                "protocol": "anthropic_messages",
                "base_url": "https://flash.test/anthropic",
                "path": "/v1/messages",
                "upstream_model": "upstream-flash",
                "api_key_env": "TEST_UPSTREAM_KEY",
                "rate_limit": {"requests_per_minute": 60, "burst": 10},
            },
        },
    }
    raw.update(overrides)
    return GatewayConfig.model_validate(raw)


def responses_payload(text: str, *, status: str = "completed") -> dict:
    return {
        "id": "resp_test",
        "object": "response",
        "status": status,
        "model": "upstream-pro",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
        "usage": {
            "input_tokens": 10,
            "input_tokens_details": {"cached_tokens": 3},
            "output_tokens": 5,
            "output_tokens_details": {"reasoning_tokens": 2},
        },
    }


def anthropic_payload(text: str, *, tool_input: dict | None = None) -> dict:
    if tool_input is not None:
        content = [
            {"type": "tool_use", "id": "toolu_test", "name": "structured_output", "input": tool_input}
        ]
        stop_reason = "tool_use"
    else:
        content = [
            {"type": "thinking", "thinking": "hidden", "signature": "sig"},
            {"type": "text", "text": text},
        ]
        stop_reason = "end_turn"
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "upstream-flash",
        "content": content,
        "stop_reason": stop_reason,
        "usage": {
            "input_tokens": 8,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 2,
            "output_tokens": 5,
        },
    }


def responses_sse(text: str) -> str:
    events = [
        {
            "type": "response.created",
            "response": {"id": "resp_stream", "status": "in_progress"},
        },
    ]
    for piece in (text[:2], text[2:]):
        if piece:
            events.append({"type": "response.output_text.delta", "delta": piece})
    events.append(
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "usage": {
                    "input_tokens": 11,
                    "input_tokens_details": {"cached_tokens": 1},
                    "output_tokens": 6,
                    "output_tokens_details": {"reasoning_tokens": 3},
                },
            },
        }
    )
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)


def anthropic_sse(text: str) -> str:
    events = [
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 11,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 1,
                    "output_tokens": 0,
                }
            },
        },
    ]
    for piece in (text[:2], text[2:]):
        if piece:
            events.append(
                {"type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}
            )
    events.append(
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 6},
        }
    )
    events.append({"type": "message_stop"})
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)


@asynccontextmanager
async def gateway_client(config: GatewayConfig, handler: Callable[[httpx.Request], httpx.Response]):
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(config, http_client=upstream)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gateway"
            ) as client:
                yield client
    finally:
        await upstream.aclose()


@pytest.fixture(autouse=True)
def upstream_key(monkeypatch):
    monkeypatch.setenv("TEST_UPSTREAM_KEY", "test-secret")

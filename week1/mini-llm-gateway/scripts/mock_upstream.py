"""Deterministic mock upstream for both protocols.

It emulates the subset of OpenAI Responses and Anthropic Messages used by the
gateway, and can inject failures based on magic strings in the user prompt:

    __FAIL_ONCE__         first request returns HTTP 503
    __FAIL_TWICE__        first two requests return HTTP 503
    __RATE_LIMIT_ONCE__   first request returns HTTP 429

Run: python scripts/mock_upstream.py --port 9001
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Mock LLM Upstream")

_LOCK = threading.Lock()
_COUNTERS: dict[str, int] = {}

_USAGE = {"prompt": 100, "completion": 20, "cached": 7, "reasoning": 5}

_FAULTS = {
    "__FAIL_TWICE__": ("fail", 2),
    "__FAIL_ONCE__": ("fail", 1),
    "__RATE_LIMIT_ONCE__": ("rate", 1),
}


def bump(key: str) -> int:
    with _LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0) + 1
        return _COUNTERS[key]


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "") for block in content if isinstance(block, dict)
        )
    return ""


def extract_text(body: dict[str, Any], protocol: str) -> str:
    if protocol == "responses":
        system = body.get("instructions") or ""
        payload = body.get("input")
        if isinstance(payload, str):
            return f"{system}\n{payload}".strip()
        user = ""
        for item in reversed(payload or []):
            if item.get("role") == "user":
                user = content_text(item.get("content"))
                break
        return f"{system}\n{user}".strip()

    system = body.get("system") or ""
    system = content_text(system)
    user = ""
    for message in reversed(body.get("messages") or []):
        if message.get("role") == "user":
            user = content_text(message.get("content"))
            break
    return f"{system}\n{user}".strip()


def value_for(schema: dict[str, Any], name: str = "value") -> Any:
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "integer":
        return 42
    if kind == "number":
        return 3.14
    if kind == "boolean":
        return True
    if kind == "array":
        return [value_for(schema.get("items") or {}, name)]
    if kind == "object":
        properties = schema.get("properties") or {}
        return {key: value_for(value, key) for key, value in properties.items()}
    return f"mock-{name}"


def mock_output(body: dict[str, Any], protocol: str, text: str) -> dict[str, Any]:
    if protocol == "responses":
        fmt = (body.get("text") or {}).get("format") or {}
        if fmt.get("type") == "json_schema":
            return {
                "text": json.dumps(value_for(fmt.get("schema") or {}), ensure_ascii=False),
                "tool": False,
                "schema": fmt.get("schema") or {},
            }
        if fmt.get("type") == "json_object":
            return {"text": '{"ok": true}', "tool": False, "schema": {}}
        return {"text": f"[{body.get('model')}] mock echo: {text}", "tool": False, "schema": {}}

    tools = body.get("tools") or []
    tool_choice = body.get("tool_choice") or {}
    if tools and tool_choice.get("type") == "tool":
        schema = tools[0].get("input_schema") or {}
        return {
            "text": json.dumps(value_for(schema), ensure_ascii=False),
            "tool": True,
            "tool_name": tool_choice.get("name") or tools[0].get("name"),
            "schema": schema,
        }
    if "single valid json object" in content_text(body.get("system")).lower():
        return {"text": '{"ok": true}', "tool": False, "schema": {}}
    return {"text": f"[{body.get('model')}] mock echo: {text}", "tool": False, "schema": {}}


def fault_error(kind: str) -> JSONResponse:
    if kind == "rate":
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": "0"},
            content={"error": {"message": "mock injected rate limit", "type": "rate_limit_error"}},
        )
    return JSONResponse(
        status_code=503,
        content={"error": {"message": "mock injected temporary failure", "type": "server_error"}},
    )


def check_fault(text: str) -> JSONResponse | None:
    for token, (kind, limit) in _FAULTS.items():
        if token in text:
            count = bump(f"fault:{kind}:{token}")
            if count <= limit:
                return fault_error(kind)
    return None


def sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def chunk_text(text: str, size: int = 8) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


@app.post("/v1/responses")
async def responses(request: Request):
    body = await request.json()
    protocol = "responses"
    bump(f"requests:{protocol}:{body.get('model')}")
    text = extract_text(body, protocol)
    fault = check_fault(text)
    if fault is not None:
        return fault

    output = mock_output(body, protocol, text)
    created = int(time.time())

    if not body.get("stream"):
        return {
            "id": f"resp_mock_{created}",
            "object": "response",
            "created_at": created,
            "status": "completed",
            "model": body.get("model"),
            "output": [
                {
                    "type": "reasoning",
                    "id": "reasoning_mock",
                    "status": "completed",
                    "content": [{"type": "reasoning_text", "text": "mock reasoning"}],
                },
                {
                    "type": "message",
                    "id": "message_mock",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": output["text"]}],
                },
            ],
            "usage": {
                "input_tokens": _USAGE["prompt"],
                "input_tokens_details": {"cached_tokens": _USAGE["cached"]},
                "output_tokens": _USAGE["completion"],
                "output_tokens_details": {"reasoning_tokens": _USAGE["reasoning"]},
                "total_tokens": _USAGE["prompt"] + _USAGE["completion"],
            },
        }

    async def generate():
        base = {"id": f"resp_mock_{created}", "object": "response", "created_at": created}
        yield sse("response.created", {"type": "response.created", "response": {**base, "status": "in_progress"}})
        yield sse("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": {"type": "message", "role": "assistant", "content": []}})
        for piece in chunk_text(output["text"]):
            await asyncio.sleep(0.02)
            yield sse(
                "response.output_text.delta",
                {"type": "response.output_text.delta", "output_index": 0, "delta": piece},
            )
        usage = {
            "input_tokens": _USAGE["prompt"],
            "input_tokens_details": {"cached_tokens": _USAGE["cached"]},
            "output_tokens": _USAGE["completion"],
            "output_tokens_details": {"reasoning_tokens": _USAGE["reasoning"]},
            "total_tokens": _USAGE["prompt"] + _USAGE["completion"],
        }
        yield sse(
            "response.completed",
            {
                "type": "response.completed",
                "response": {**base, "status": "completed", "usage": usage},
            },
        )
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/anthropic/v1/messages")
async def anthropic_messages(request: Request):
    body = await request.json()
    protocol = "anthropic"
    bump(f"requests:{protocol}:{body.get('model')}")
    text = extract_text(body, protocol)
    fault = check_fault(text)
    if fault is not None:
        return fault

    output = mock_output(body, protocol, text)
    created = int(time.time())

    if not body.get("stream"):
        if output["tool"]:
            content = [
                {
                    "type": "tool_use",
                    "id": "toolu_mock",
                    "name": output["tool_name"],
                    "input": json.loads(output["text"]),
                }
            ]
            stop_reason = "tool_use"
        else:
            content = [
                {"type": "thinking", "thinking": "mock thinking", "signature": "mock"},
                {"type": "text", "text": output["text"]},
            ]
            stop_reason = "end_turn"
        return {
            "id": f"msg_mock_{created}",
            "type": "message",
            "role": "assistant",
            "model": body.get("model"),
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": _USAGE["prompt"] - _USAGE["cached"],
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": _USAGE["cached"],
                "output_tokens": _USAGE["completion"],
            },
        }

    async def generate():
        usage_start = {
            "input_tokens": _USAGE["prompt"] - _USAGE["cached"],
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": _USAGE["cached"],
            "output_tokens": 0,
        }
        yield sse(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": f"msg_mock_{created}",
                    "type": "message",
                    "role": "assistant",
                    "model": body.get("model"),
                    "content": [],
                    "stop_reason": None,
                    "usage": usage_start,
                },
            },
        )
        if output["tool"]:
            yield sse(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "tool_use", "id": "toolu_mock", "name": output["tool_name"], "input": {}},
                },
            )
            for piece in chunk_text(output["text"], 10):
                await asyncio.sleep(0.02)
                yield sse(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": piece}},
                )
            stop_reason = "tool_use"
        else:
            yield sse(
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            )
            for piece in chunk_text(output["text"]):
                await asyncio.sleep(0.02)
                yield sse(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": piece}},
                )
            stop_reason = "end_turn"
        yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
        yield sse(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {"output_tokens": _USAGE["completion"]},
            },
        )
        yield sse("message_stop", {"type": "message_stop"})
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/__stats")
async def stats():
    with _LOCK:
        counts = dict(_COUNTERS)
    return {"counts": counts}


@app.post("/__reset")
async def reset():
    with _LOCK:
        _COUNTERS.clear()
    return {"ok": True}


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock LLM upstream")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9001)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

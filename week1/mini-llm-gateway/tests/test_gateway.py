from __future__ import annotations

import json

import httpx

from tests.conftest import (
    anthropic_payload,
    anthropic_sse,
    gateway_client,
    make_config,
    responses_payload,
    responses_sse,
)

SCHEMA = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
    "additionalProperties": False,
}


def test_routes_by_model_and_masks_protocol_differences(tmp_path):
    seen: list[tuple[str, dict, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append((request.url.host, payload, dict(request.headers)))
        if request.url.host == "pro.test":
            return httpx.Response(200, json=responses_payload("pro answer"))
        return httpx.Response(200, json=anthropic_payload("flash answer"))

    async def run():
        async with gateway_client(make_config(tmp_path), handler) as client:
            pro = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "pro",
                    "messages": [
                        {"role": "system", "content": "Be terse."},
                        {"role": "user", "content": "hello"},
                    ],
                },
            )
            flash = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "flash",
                    "messages": [
                        {"role": "system", "content": "Be terse."},
                        {"role": "user", "content": "hello"},
                    ],
                },
            )
            assert pro.status_code == 200
            assert pro.json()["protocol"] == "openai_responses"
            assert pro.json()["choices"][0]["message"]["content"] == "pro answer"
            assert flash.status_code == 200
            assert flash.json()["protocol"] == "anthropic_messages"
            assert flash.json()["choices"][0]["message"]["content"] == "flash answer"

    import asyncio

    asyncio.run(run())

    pro_host, pro_body, pro_headers = seen[0]
    flash_host, flash_body, flash_headers = seen[1]
    assert pro_host == "pro.test"
    assert pro_body["instructions"] == "Be terse."
    assert pro_body["input"][0] == {"role": "user", "content": "hello"}
    assert pro_headers["authorization"] == "Bearer test-secret"

    assert flash_host == "flash.test"
    assert flash_body["system"] == "Be terse."
    assert flash_body["messages"][0] == {"role": "user", "content": "hello"}
    assert flash_headers["x-api-key"] == "test-secret"
    assert flash_headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in flash_headers


def test_streaming_sse_and_ttft_are_recorded(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "pro.test":
            return httpx.Response(
                200,
                content=responses_sse("Hello"),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            content=anthropic_sse("Hello"),
            headers={"content-type": "text/event-stream"},
        )

    async def run():
        async with gateway_client(make_config(tmp_path), handler) as client:
            for model in ("pro", "flash"):
                async with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": "hi"}],
                        "stream": True,
                    },
                ) as response:
                    assert response.status_code == 200
                    body = (await response.aread()).decode()
                    request_id = response.headers["x-request-id"]

                assert '"content": "He"' in body or '"content":"He"' in body
                assert "data: [DONE]" in body
                assert '"usage"' in body

                usage = (await client.get(f"/admin/usage/{request_id}")).json()
                assert usage["stream"] is True
                assert usage["status"] == "success"
                assert usage["ttft_ms"] is not None
                assert usage["latency_ms"] >= usage["ttft_ms"]
                assert usage["prompt_tokens"] > 0
                assert usage["completion_tokens"] > 0

    import asyncio

    asyncio.run(run())


def test_structured_output_is_validated_and_repaired(tmp_path):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = json.loads(request.content)
        assert payload["text"]["format"]["type"] == "json_schema"
        if calls == 1:
            return httpx.Response(200, json=responses_payload('{"name": 123}'))
        assert "failed JSON Schema validation" in payload["input"][-1]["content"]
        return httpx.Response(200, json=responses_payload('{"name": "Ada"}'))

    async def run():
        async with gateway_client(make_config(tmp_path), handler) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "pro",
                    "messages": [{"role": "user", "content": "Extract a name"}],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "person", "strict": True, "schema": SCHEMA},
                    },
                },
            )
            assert response.status_code == 200
            body = response.json()
            assert body["structured_output"] == {"name": "Ada"}
            assert body["retries"] == 1
            usage = (await client.get(f"/admin/usage/{body['request_id']}")).json()
            assert usage["retries"] == 1

    import asyncio

    asyncio.run(run())
    assert calls == 2


def test_structured_output_failure_returns_422(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=responses_payload('{"name": 123}'))

    async def run():
        async with gateway_client(make_config(tmp_path, structured_repair_attempts=0), handler) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "pro",
                    "messages": [{"role": "user", "content": "Extract a name"}],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {"name": "person", "schema": SCHEMA},
                    },
                },
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "structured_output_invalid"

    import asyncio

    asyncio.run(run())


def test_prompt_versions_and_variable_rendering(tmp_path):
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json=responses_payload("ok"))

    async def run():
        async with gateway_client(make_config(tmp_path), handler) as client:
            first = await client.post(
                "/v1/prompts",
                json={
                    "id": "reviewer",
                    "name": "Reviewer",
                    "content": "Review {{ language }} code.",
                    "activate": True,
                },
            )
            assert first.json()["version"] == 1

            second = await client.post(
                "/v1/prompts",
                json={
                    "id": "reviewer",
                    "name": "Reviewer",
                    "content": "Strictly review {{ language }} code.",
                    "activate": True,
                },
            )
            assert second.json()["version"] == 2

            rendered = await client.post(
                "/v1/prompts/reviewer/render",
                json={"version": 1, "variables": {"language": "Python"}},
            )
            assert rendered.json()["content"] == "Review Python code."

            explicit = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "pro",
                    "messages": [{"role": "user", "content": "this code"}],
                    "prompt": {
                        "id": "reviewer",
                        "version": 1,
                        "variables": {"language": "Python"},
                    },
                },
            )
            assert explicit.json()["prompt"] == {"id": "reviewer", "version": 1}

            active = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "pro",
                    "messages": [{"role": "user", "content": "this code"}],
                    "prompt": {"id": "reviewer", "variables": {"language": "Go"}},
                },
            )
            assert active.json()["prompt"] == {"id": "reviewer", "version": 2}

    import asyncio

    asyncio.run(run())
    assert bodies[0]["instructions"] == "Review Python code."
    assert bodies[1]["instructions"] == "Strictly review Go code."


def test_retry_with_exponential_backoff(tmp_path):
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503, json={"error": {"message": "temporary"}})
        return httpx.Response(200, json=responses_payload("recovered"))

    async def run():
        async with gateway_client(make_config(tmp_path), handler) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "pro", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert response.status_code == 200
            body = response.json()
            assert body["choices"][0]["message"]["content"] == "recovered"
            assert body["retries"] == 2
            usage = (await client.get(f"/admin/usage/{body['request_id']}")).json()
            assert usage["retries"] == 2

    import asyncio

    asyncio.run(run())
    assert calls == 3


def test_rate_limit_is_isolated_per_model(tmp_path):
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        if request.url.host == "pro.test":
            return httpx.Response(200, json=responses_payload("ok"))
        return httpx.Response(200, json=anthropic_payload("ok"))

    models = {
        "pro": {
            "protocol": "openai_responses",
            "base_url": "https://pro.test",
            "upstream_model": "upstream-pro",
            "api_key_env": "TEST_UPSTREAM_KEY",
            "rate_limit": {"requests_per_minute": 1, "burst": 1},
        },
        "flash": {
            "protocol": "anthropic_messages",
            "base_url": "https://flash.test/anthropic",
            "upstream_model": "upstream-flash",
            "api_key_env": "TEST_UPSTREAM_KEY",
            "rate_limit": {"requests_per_minute": 60, "burst": 5},
        },
    }

    async def run():
        async with gateway_client(make_config(tmp_path, models=models), handler) as client:
            payload = {"messages": [{"role": "user", "content": "hi"}]}
            first = await client.post("/v1/chat/completions", json={"model": "pro", **payload})
            second = await client.post("/v1/chat/completions", json={"model": "pro", **payload})
            flash = await client.post("/v1/chat/completions", json={"model": "flash", **payload})

            assert first.status_code == 200
            assert second.status_code == 429
            assert second.json()["error"]["code"] == "rate_limit_exceeded"
            assert int(second.headers["retry-after"]) >= 1
            assert flash.status_code == 200

            limits = (await client.get("/admin/rate-limits")).json()
            assert limits["pro"]["tokens"] < 1
            assert limits["flash"]["tokens"] >= 3.5

    import asyncio

    asyncio.run(run())
    assert calls == ["pro.test", "flash.test"]


def test_error_envelope_metrics_and_gateway_auth(tmp_path):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    async def run():
        config = make_config(tmp_path, api_keys=["gateway-secret"])
        async with gateway_client(config, handler) as client:
            unauthorized = await client.post(
                "/v1/chat/completions",
                json={"model": "pro", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert unauthorized.status_code == 401
            assert unauthorized.json()["error"]["code"] == "invalid_api_key"

            headers = {"Authorization": "Bearer gateway-secret"}
            unknown = await client.post(
                "/v1/chat/completions",
                headers=headers,
                json={"model": "missing", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert unknown.status_code == 404
            assert unknown.json()["error"]["code"] == "model_not_found"

            upstream_auth = await client.post(
                "/v1/chat/completions",
                headers=headers,
                json={"model": "pro", "messages": [{"role": "user", "content": "hi"}]},
            )
            assert upstream_auth.status_code == 502
            assert upstream_auth.json()["error"]["code"] == "upstream_auth_error"

            metrics = (await client.get("/admin/metrics", headers=headers)).json()
            assert metrics["models"][0]["model"] == "pro"
            assert metrics["models"][0]["errors"] == 1
            assert "rate_limits" in metrics

    import asyncio

    asyncio.run(run())

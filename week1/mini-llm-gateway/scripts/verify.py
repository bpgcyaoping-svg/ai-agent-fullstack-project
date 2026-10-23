"""End-to-end verification for mini-llm-gateway.

Two modes:

    python scripts/verify.py --mode mock     # deterministic mock upstream, no API key
    python scripts/verify.py --mode real     # real DeepSeek endpoints, needs DEEPSEEK_API_KEY

Every check prints PASS/FAIL and the full evidence is written to
evidence/verify_<mode>.json. The process exits with 1 if any check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT / "evidence"
RESULTS: list[dict[str, Any]] = []

SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "integer"},
    },
    "required": ["answer", "confidence"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def load_env_file(path: Path | None) -> dict[str, str]:
    if path is None or not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def tail(path: Path, lines: int = 25) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


async def wait_for(url: str, attempts: int = 200, delay: float = 0.1) -> None:
    last: str | None = None
    async with httpx.AsyncClient(timeout=3) as client:
        for _ in range(attempts):
            try:
                response = await client.get(url)
                if response.status_code < 500:
                    return
                last = f"HTTP {response.status_code}"
            except Exception as exc:  # noqa: BLE001 - startup race is expected
                last = repr(exc)
            await asyncio.sleep(delay)
    raise RuntimeError(f"{url} did not become ready: {last}")


class Service:
    """Small subprocess wrapper with a log file and ready-endpoint polling."""

    def __init__(
        self,
        name: str,
        command: list[str],
        env: dict[str, str],
        ready_url: str,
        log_path: Path,
    ) -> None:
        self.name = name
        self.command = command
        self.env = env
        self.ready_url = ready_url
        self.log_path = log_path
        self.process: subprocess.Popen[bytes] | None = None

    async def start(self) -> None:
        log = self.log_path.open("w", encoding="utf-8")
        self.process = subprocess.Popen(
            self.command,
            cwd=str(ROOT),
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            await wait_for(self.ready_url)
        except Exception:
            self.stop()
            raise RuntimeError(
                f"{self.name} failed to start ({self.ready_url})\n{tail(self.log_path)}"
            ) from None

    def stop(self) -> None:
        if self.process is None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process = None


def write_mock_config(
    path: Path, mock_base: str, database: Path, *, rpm: int = 600, burst: int = 100
) -> None:
    config = {
        "service_name": "mini-llm-gateway",
        "database_path": str(database),
        "retry": {
            "max_attempts": 3,
            "base_delay_seconds": 0.05,
            "max_delay_seconds": 0.2,
            "retry_statuses": [408, 409, 429, 500, 502, 503, 504],
        },
        "structured_repair_attempts": 1,
        "models": {
            "deepseek-v4-pro": {
                "protocol": "openai_responses",
                "base_url": mock_base,
                "path": "/v1/responses",
                "upstream_model": "deepseek-v4-pro",
                "api_key_env": "DEEPSEEK_API_KEY",
                "timeout_seconds": 30,
                "default_max_tokens": 256,
                "rate_limit": {"requests_per_minute": rpm, "burst": burst},
            },
            "deepseek-flash": {
                "protocol": "anthropic_messages",
                "base_url": mock_base,
                "path": "/anthropic/v1/messages",
                "upstream_model": "deepseek-flash",
                "api_key_env": "DEEPSEEK_API_KEY",
                "timeout_seconds": 30,
                "default_max_tokens": 256,
                "rate_limit": {"requests_per_minute": rpm, "burst": burst},
            },
        },
    }
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def write_real_config(path: Path, database: Path, *, rpm: int, burst: int) -> None:
    raw = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    raw["database_path"] = str(database)
    for model in raw["models"].values():
        model["rate_limit"] = {"requests_per_minute": rpm, "burst": burst}
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")


def gateway_command(port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]


def record(name: str, ok: bool, detail: dict[str, Any]) -> None:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    if ok:
        print(f"PASS  {name}")
    else:
        print(f"FAIL  {name}: {json.dumps(detail, ensure_ascii=False)[:600]}")


async def run_check(name: str, factory) -> None:
    try:
        ok, detail = await factory()
    except Exception as exc:  # noqa: BLE001 - report and keep going
        ok, detail = False, {"error": f"{type(exc).__name__}: {exc}"}
    record(name, ok, detail)


def chat_body(model: str, text: str, **extra: Any) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "max_tokens": 64,
        **extra,
    }


async def consume_sse(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any]
) -> dict[str, Any]:
    started = time.perf_counter()
    chunks: list[str] = []
    final: dict[str, Any] = {}
    ttft: float | None = None
    done = False
    status = 0
    request_id = None

    async with client.stream("POST", url, json=payload) as response:
        status = response.status_code
        request_id = response.headers.get("x-request-id")
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            if raw == "[DONE]":
                done = True
                continue
            if not raw:
                continue
            data = json.loads(raw)
            delta = (data.get("choices") or [{}])[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                if ttft is None:
                    ttft = round((time.perf_counter() - started) * 1000, 2)
                chunks.append(piece)
            if data.get("usage"):
                final = data

    return {
        "status": status,
        "request_id": request_id,
        "text": "".join(chunks),
        "ttft_ms": ttft,
        "done": done,
        "final": final,
    }


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
async def check_models(client: httpx.AsyncClient, base: str):
    response = await client.get(f"{base}/v1/models")
    payload = response.json()
    protocols = {item["id"]: item["protocol"] for item in payload.get("data", [])}
    ok = (
        response.status_code == 200
        and protocols.get("deepseek-v4-pro") == "openai_responses"
        and protocols.get("deepseek-flash") == "anthropic_messages"
    )
    return ok, {"status": response.status_code, "models": payload.get("data")}


async def check_completion(client: httpx.AsyncClient, base: str, model: str):
    response = await client.post(
        f"{base}/v1/chat/completions",
        json=chat_body(model, "Reply with the single word: pong"),
    )
    payload = response.json()
    content = (payload.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    ok = response.status_code == 200 and bool(content.strip())
    return ok, {
        "status": response.status_code,
        "content": content,
        "protocol": payload.get("protocol"),
        "upstream_model": payload.get("upstream_model"),
        "usage": payload.get("usage"),
        "timing": payload.get("timing"),
        "request_id": payload.get("request_id"),
    }


async def check_stream(client: httpx.AsyncClient, base: str, model: str):
    result = await consume_sse(
        client,
        f"{base}/v1/chat/completions",
        chat_body(model, "Count from 1 to 5 in one line.", stream=True, max_tokens=64),
    )
    ok = (
        result["status"] == 200
        and result["done"]
        and bool(result["text"])
        and result["ttft_ms"] is not None
    )
    return ok, {
        "status": result["status"],
        "chunks": len(result["text"]),
        "text": result["text"][:300],
        "ttft_ms": result["ttft_ms"],
        "done": result["done"],
        "usage": result["final"].get("usage"),
        "timing": result["final"].get("timing"),
        "request_id": result["request_id"],
    }


async def check_structured(client: httpx.AsyncClient, base: str, model: str):
    response = await client.post(
        f"{base}/v1/chat/completions",
        json=chat_body(
            model,
            "Return JSON with answer='42' and confidence=99.",
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "answer", "schema": SCHEMA},
            },
        ),
    )
    payload = response.json()
    raw = (payload.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    valid = (
        isinstance(parsed, dict)
        and isinstance(parsed.get("answer"), str)
        and isinstance(parsed.get("confidence"), int)
    )
    ok = response.status_code == 200 and valid
    return ok, {
        "status": response.status_code,
        "content": raw[:300],
        "parsed": parsed,
        "structured_output": payload.get("structured_output"),
        "error": payload.get("error"),
    }


async def check_prompt_versions(client: httpx.AsyncClient, base: str):
    v1 = await client.post(
        f"{base}/v1/prompts",
        json={
            "id": "verify-analyst",
            "name": "Verify analyst",
            "role": "system",
            "content": "You are a {{ tone }} analyst. Answer in {{ language }}.",
        },
    )
    v2 = await client.post(
        f"{base}/v1/prompts",
        json={
            "id": "verify-analyst",
            "name": "Verify analyst",
            "role": "system",
            "content": "You are a {{ tone }} analyst. This is version two.",
        },
    )
    render_v1 = await client.post(
        f"{base}/v1/prompts/verify-analyst/render",
        json={"version": 1, "variables": {"tone": "strict", "language": "English"}},
    )
    render_active = await client.post(
        f"{base}/v1/prompts/verify-analyst/render",
        json={"variables": {"tone": "friendly"}},
    )
    chat = await client.post(
        f"{base}/v1/chat/completions",
        json=chat_body(
            "deepseek-v4-pro",
            "Say ok.",
            prompt={
                "id": "verify-analyst",
                "version": 1,
                "variables": {"tone": "strict", "language": "English"},
            },
        ),
    )
    request_id = chat.json().get("request_id") if chat.status_code == 200 else None
    usage = None
    if request_id:
        usage = (await client.get(f"{base}/admin/usage/{request_id}")).json()

    v1_text = render_v1.json().get("content")
    active_text = render_active.json().get("content")
    ok = (
        v1.status_code == 201
        and v2.status_code == 201
        and v2.json().get("version") == 2
        and v1_text == "You are a strict analyst. Answer in English."
        and active_text == "You are a friendly analyst. This is version two."
        and chat.status_code == 200
        and usage is not None
        and usage.get("prompt_id") == "verify-analyst"
        and usage.get("prompt_version") == 1
    )
    return ok, {
        "v1_version": v1.json().get("version"),
        "v2_version": v2.json().get("version"),
        "render_v1": v1_text,
        "render_active": active_text,
        "chat_prompt_meta": chat.json().get("prompt") if chat.status_code == 200 else None,
        "usage_prompt": {"id": usage.get("prompt_id"), "version": usage.get("prompt_version")}
        if usage
        else None,
    }


async def check_observability(client: httpx.AsyncClient, base: str):
    await asyncio.sleep(0.3)  # let stream usage records flush
    usage_response = await client.get(f"{base}/admin/usage", params={"limit": 100})
    metrics_response = await client.get(f"{base}/admin/metrics")
    records = usage_response.json().get("data", [])
    metrics = metrics_response.json()
    streamed = [item for item in records if item["stream"]]
    with_ttft = [item for item in streamed if item.get("ttft_ms") is not None]
    models = {row["model"]: row for row in metrics.get("models", [])}
    totals = metrics.get("totals", {})
    ok = (
        usage_response.status_code == 200
        and len(records) >= 6
        and bool(with_ttft)
        and all(item["latency_ms"] > 0 for item in records)
        and all("prompt_tokens" in item and "completion_tokens" in item for item in records)
        and totals.get("total_tokens", 0) > 0
        and models.get("deepseek-v4-pro", {}).get("protocol") == "openai_responses"
        and models.get("deepseek-flash", {}).get("protocol") == "anthropic_messages"
        and all(
            key in models.get("deepseek-v4-pro", {})
            for key in ("cached_tokens", "reasoning_tokens", "avg_latency_ms", "avg_ttft_ms")
        )
    )
    return ok, {
        "records": len(records),
        "stream_records": len(streamed),
        "records_with_ttft": len(with_ttft),
        "sample": records[0] if records else None,
        "totals": totals,
        "model_metrics": list(models.values()),
    }


async def check_error_envelope(client: httpx.AsyncClient, base: str):
    response = await client.post(
        f"{base}/v1/chat/completions",
        json=chat_body("unknown-model", "hello"),
    )
    payload = response.json()
    error = payload.get("error", {})
    ok = (
        response.status_code == 404
        and error.get("code") == "model_not_found"
        and {"message", "type", "code", "param", "request_id"} <= set(error)
    )
    return ok, {"status": response.status_code, "error": payload}


async def check_retry(client: httpx.AsyncClient, base: str, mock_base: str):
    stats_before = (await client.get(f"{mock_base}/__stats")).json()["counts"]
    response = await client.post(
        f"{base}/v1/chat/completions",
        json=chat_body("deepseek-v4-pro", "__FAIL_TWICE__ then say ok."),
    )
    payload = response.json()
    request_id = payload.get("request_id")
    usage = (await client.get(f"{base}/admin/usage/{request_id}")).json() if request_id else {}
    stats_after = (await client.get(f"{mock_base}/__stats")).json()["counts"]
    key = "requests:responses:deepseek-v4-pro"
    delta = stats_after.get(key, 0) - stats_before.get(key, 0)
    ok = (
        response.status_code == 200
        and usage.get("retries") == 2
        and delta == 3
    )
    return ok, {
        "status": response.status_code,
        "content": (payload.get("choices") or [{}])[0].get("message", {}).get("content") if response.status_code == 200 else None,
        "usage_retries": usage.get("retries"),
        "upstream_attempts": delta,
        "expected_attempts": 3,
        "error": payload.get("error"),
    }


async def check_rate_limit(strict_base: str):
    async with httpx.AsyncClient(timeout=120) as client:
        first = await client.post(
            f"{strict_base}/v1/chat/completions",
            json=chat_body("deepseek-v4-pro", "Say ok."),
        )
        second = await client.post(
            f"{strict_base}/v1/chat/completions",
            json=chat_body("deepseek-v4-pro", "Say ok."),
        )
        other_model = await client.post(
            f"{strict_base}/v1/chat/completions",
            json=chat_body("deepseek-flash", "Say ok."),
        )
    error = second.json().get("error", {})
    ok = (
        first.status_code == 200
        and second.status_code == 429
        and bool(second.headers.get("retry-after"))
        and error.get("code") == "rate_limit_exceeded"
        and other_model.status_code == 200
    )
    return ok, {
        "pro_first": first.status_code,
        "pro_second": second.status_code,
        "retry_after": second.headers.get("retry-after"),
        "error": error,
        "flash_after_pro_limited": other_model.status_code,
    }


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
async def run(args: argparse.Namespace) -> int:
    mode = args.mode
    temp_dir = Path(tempfile.mkdtemp(prefix="mini-gateway-verify-"))
    services: list[Service] = []
    started_at = datetime.now(timezone.utc)

    if mode == "mock":
        mock_port = free_port()
        mock_base = f"http://127.0.0.1:{mock_port}"
        mock_env = {**os.environ, "DEEPSEEK_API_KEY": "mock-secret"}
        mock = Service(
            "mock-upstream",
            [sys.executable, str(ROOT / "scripts" / "mock_upstream.py"), "--port", str(mock_port)],
            mock_env,
            f"{mock_base}/__stats",
            temp_dir / "mock.log",
        )
        await mock.start()
        services.append(mock)
        write_mock_config(temp_dir / "main.yaml", mock_base, temp_dir / "main.db")
        write_mock_config(
            temp_dir / "strict.yaml", mock_base, temp_dir / "strict.db", rpm=1, burst=1
        )
        gateway_env = {**mock_env, "DEEPSEEK_API_KEY": "mock-secret"}
    else:
        env_file = args.env_file
        if env_file is None:
            candidates = [ROOT / ".env", ROOT.parent.parent / ".env"]
            env_file = next((path for path in candidates if path.is_file()), None)
        file_env = load_env_file(env_file)
        gateway_env = {**os.environ, **file_env}
        key = gateway_env.get("DEEPSEEK_API_KEY", "")
        if not key or key.startswith("replace"):
            raise SystemExit(
                "real mode needs DEEPSEEK_API_KEY (set --env-file or export the variable)"
            )
        write_real_config(temp_dir / "main.yaml", temp_dir / "main.db", rpm=600, burst=100)
        write_real_config(temp_dir / "strict.yaml", temp_dir / "strict.db", rpm=1, burst=1)
        mock_base = None

    main_port = free_port()
    main_base = f"http://127.0.0.1:{main_port}"
    main_service = Service(
        "gateway",
        gateway_command(main_port),
        {**gateway_env, "GATEWAY_CONFIG": str(temp_dir / "main.yaml")},
        f"{main_base}/healthz",
        temp_dir / "gateway.log",
    )
    await main_service.start()
    services.append(main_service)

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0)) as client:
            await run_check("model_routing_endpoint", lambda: check_models(client, main_base))
            for model in ("deepseek-v4-pro", "deepseek-flash"):
                await run_check(
                    f"non_stream::{model}",
                    lambda model=model: check_completion(client, main_base, model),
                )
            for model in ("deepseek-v4-pro", "deepseek-flash"):
                await run_check(
                    f"streaming_sse::{model}",
                    lambda model=model: check_stream(client, main_base, model),
                )
            for model in ("deepseek-v4-pro", "deepseek-flash"):
                await run_check(
                    f"structured_output::{model}",
                    lambda model=model: check_structured(client, main_base, model),
                )
            await run_check(
                "prompt_versions_and_reference", lambda: check_prompt_versions(client, main_base)
            )
            await run_check("observability", lambda: check_observability(client, main_base))
            await run_check("error_envelope", lambda: check_error_envelope(client, main_base))
            if mode == "mock":
                await run_check(
                    "retry_exponential_backoff",
                    lambda: check_retry(client, main_base, mock_base),
                )

        strict_port = free_port()
        strict_base = f"http://127.0.0.1:{strict_port}"
        strict_service = Service(
            "gateway-strict",
            gateway_command(strict_port),
            {**gateway_env, "GATEWAY_CONFIG": str(temp_dir / "strict.yaml")},
            f"{strict_base}/healthz",
            temp_dir / "gateway-strict.log",
        )
        await strict_service.start()
        services.append(strict_service)
        await run_check("rate_limit_per_model", lambda: check_rate_limit(strict_base))
    finally:
        for service in reversed(services):
            service.stop()

    passed = sum(1 for item in RESULTS if item["ok"])
    failed = len(RESULTS) - passed
    evidence = {
        "mode": mode,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "passed": passed,
        "failed": failed,
        "results": RESULTS,
    }
    EVIDENCE_DIR.mkdir(exist_ok=True)
    evidence_path = EVIDENCE_DIR / f"verify_{mode}.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    print("")
    print(f"mode={mode} passed={passed} failed={failed}")
    print(f"evidence: {evidence_path}")
    return 0 if failed == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify mini-llm-gateway end to end")
    parser.add_argument("--mode", choices=["mock", "real"], default="mock")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="env file for real mode (defaults to ./.env then ../.env)",
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import httpx

from app.adapters.anthropic_messages import AnthropicMessagesAdapter
from app.adapters.base import AdapterRequest, AdapterResult, BaseAdapter, TokenUsage
from app.adapters.openai_responses import OpenAIResponsesAdapter
from app.config import GatewayConfig, ModelConfig
from app.core.errors import GatewayError, UpstreamError, map_upstream_http_error
from app.core.retry import backoff_delay
from app.schemas import ChatRequest
from app.services.observability import UsageRecord, UsageStore
from app.services.prompts import PromptRepository
from app.services.structured import StructuredOutputError, validate_structured

logger = logging.getLogger(__name__)


def build_adapter(model: ModelConfig) -> BaseAdapter:
    if model.protocol == "openai_responses":
        return OpenAIResponsesAdapter(model)
    if model.protocol == "anthropic_messages":
        return AnthropicMessagesAdapter(model)
    raise ValueError(f"Unsupported protocol: {model.protocol}")


class GatewayService:
    """Routes one unified request to the adapter selected by the model field."""

    def __init__(
        self,
        config: GatewayConfig,
        http_client: httpx.AsyncClient,
        prompts: PromptRepository,
        usage: UsageStore,
    ) -> None:
        self.config = config
        self.http = http_client
        self.prompts = prompts
        self.usage = usage
        self.adapters = {name: build_adapter(model) for name, model in config.models.items()}

    def model_config(self, model: str) -> ModelConfig:
        config = self.config.models.get(model)
        if config is None:
            raise GatewayError(
                f"Unknown model {model!r}; available models: {sorted(self.config.models)}",
                status_code=404,
                error_type="invalid_request_error",
                code="model_not_found",
                param="model",
            )
        return config

    async def prepare(self, request: ChatRequest) -> tuple[AdapterRequest, dict[str, Any] | None]:
        model = self.model_config(request.model)

        system_parts: list[str] = []
        conversation: list[dict[str, str]] = []
        for message in request.messages:
            if message.role == "system":
                system_parts.append(message.content)
            else:
                conversation.append({"role": message.role, "content": message.content})

        prompt_meta: dict[str, Any] | None = None
        if request.prompt is not None:
            record, rendered = await asyncio.to_thread(
                self.prompts.render,
                request.prompt.id,
                request.prompt.variables,
                request.prompt.version,
            )
            prompt_meta = {"id": record.id, "version": record.version, "role": record.role}
            if record.role == "system":
                system_parts.insert(0, rendered)
            else:
                conversation.insert(0, {"role": "user", "content": rendered})

        if not conversation:
            raise GatewayError(
                "At least one user or assistant message is required",
                status_code=422,
                error_type="invalid_request_error",
                code="invalid_request",
                param="messages",
            )

        system = "\n\n".join(part for part in system_parts if part).strip() or None
        internal = AdapterRequest(
            upstream_model=model.upstream_model,
            system=system,
            messages=conversation,
            temperature=request.temperature,
            max_tokens=request.max_tokens or model.default_max_tokens,
            response_format=request.response_format,
            stream=request.stream,
        )
        return internal, prompt_meta

    async def complete(self, request: ChatRequest, request_id: str) -> dict[str, Any]:
        model = self.model_config(request.model)
        internal, prompt_meta = await self.prepare(request)
        adapter = self.adapters[request.model]
        record = self._new_record(request, request_id, prompt_meta)
        retry_state = {"count": 0}
        started = time.perf_counter()

        try:
            working = internal
            repairs = 0
            parsed: Any = None
            while True:
                result = await self._request_with_retry(adapter, working, request_id, retry_state)
                self._accumulate(record, result.usage)
                record.upstream_model = result.upstream_model or model.upstream_model

                if internal.response_format is None:
                    break
                try:
                    parsed = validate_structured(result.text, internal.response_format)
                    break
                except StructuredOutputError as exc:
                    if repairs >= self.config.structured_repair_attempts:
                        raise GatewayError(
                            f"Structured output remains invalid: {exc.message}",
                            status_code=422,
                            error_type="invalid_request_error",
                            code="structured_output_invalid",
                            details={"last_output": result.text[:1000]},
                        ) from exc
                    repairs += 1
                    retry_state["count"] += 1
                    working = replace(
                        internal,
                        messages=[
                            *working.messages,
                            {"role": "assistant", "content": result.text},
                            {"role": "user", "content": exc.repair_instruction},
                        ],
                    )

            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            record.status = "success"
            record.status_code = 200
            return self._completion_payload(
                request, request_id, model, record, result, parsed, latency_ms, retry_state["count"]
            )
        except GatewayError as exc:
            record.status = "error"
            record.status_code = exc.status_code
            record.error_code = exc.code
            record.error_message = exc.message
            raise
        finally:
            record.retries = retry_state["count"]
            record.latency_ms = round((time.perf_counter() - started) * 1000, 2)
            await self._persist(record)

    async def stream(self, request: ChatRequest, request_id: str) -> AsyncIterator[bytes]:
        model = self.model_config(request.model)
        internal, prompt_meta = await self.prepare(request)
        adapter = self.adapters[request.model]
        record = self._new_record(request, request_id, prompt_meta)
        record.stream = True
        retry_state = {"count": 0}
        created = int(time.time())

        async def generate() -> AsyncIterator[bytes]:
            started = time.perf_counter()
            emitted = False
            first_token_ms: float | None = None
            text_parts: list[str] = []
            usage = TokenUsage()
            finish_reason = "stop"
            succeeded = False

            try:
                for attempt in range(self.config.retry.max_attempts):
                    parser = adapter.stream_parser()
                    prepared = adapter.prepare(internal, request_id)
                    attempt_emitted = False
                    try:
                        async with self.http.stream(
                            "POST",
                            prepared.url,
                            headers=prepared.headers,
                            json=prepared.payload,
                            timeout=model.timeout_seconds,
                        ) as response:
                            if response.status_code >= 400:
                                body = await response.aread()
                                raise map_upstream_http_error(
                                    model.upstream_model,
                                    response.status_code,
                                    body.decode("utf-8", errors="replace"),
                                    self.config.retry.retry_statuses,
                                    response.headers.get("retry-after"),
                                )

                            async for line in response.aiter_lines():
                                if not line.startswith("data:"):
                                    continue
                                raw = line[5:].strip()
                                if not raw or raw == "[DONE]":
                                    continue
                                for event in parser.feed(raw):
                                    if event.usage is not None:
                                        usage = event.usage
                                    if event.text:
                                        if not attempt_emitted:
                                            attempt_emitted = True
                                        if not emitted:
                                            emitted = True
                                            first_token_ms = round(
                                                (time.perf_counter() - started) * 1000, 2
                                            )
                                            delta = {"role": "assistant", "content": event.text}
                                        else:
                                            delta = {"content": event.text}
                                        text_parts.append(event.text)
                                        yield self._sse(
                                            {
                                                "id": request_id,
                                                "object": "chat.completion.chunk",
                                                "created": created,
                                                "model": request.model,
                                                "protocol": model.protocol,
                                                "choices": [{"index": 0, "delta": delta}],
                                            }
                                        )
                                    if event.done:
                                        finish_reason = event.finish_reason or "stop"
                                        usage = event.usage or usage

                            if not parser.done:
                                final = parser.finish()
                                usage = final.usage or usage
                                finish_reason = final.finish_reason or finish_reason
                        succeeded = True
                        break
                    except (httpx.HTTPError, GatewayError) as exc:
                        error = self._normalize_stream_error(exc, model)
                        if attempt_emitted:
                            self._mark_error(record, error)
                            yield self._sse_error(error, request_id)
                            yield b"data: [DONE]\n\n"
                            return
                        if not error.retryable or attempt + 1 >= self.config.retry.max_attempts:
                            self._mark_error(record, error)
                            yield self._sse_error(error, request_id)
                            yield b"data: [DONE]\n\n"
                            return
                        retry_state["count"] += 1
                        await asyncio.sleep(
                            backoff_delay(
                                attempt,
                                self.config.retry.base_delay_seconds,
                                self.config.retry.max_delay_seconds,
                            )
                        )

                if not succeeded:
                    error = GatewayError(
                        "All upstream attempts failed",
                        status_code=502,
                        error_type="upstream_error",
                        code="upstream_error",
                    )
                    self._mark_error(record, error)
                    yield self._sse_error(error, request_id)
                    yield b"data: [DONE]\n\n"
                    return

                if internal.response_format is not None and text_parts:
                    try:
                        validate_structured("".join(text_parts), internal.response_format)
                    except StructuredOutputError as exc:
                        self._mark_error(record, exc)
                        yield self._sse_error(exc, request_id)
                        yield b"data: [DONE]\n\n"
                        return

                latency_ms = round((time.perf_counter() - started) * 1000, 2)
                self._accumulate(record, usage)
                record.upstream_model = model.upstream_model
                record.ttft_ms = first_token_ms
                record.status = "success"
                record.status_code = 200
                record.latency_ms = latency_ms
                record.retries = retry_state["count"]
                yield self._sse(
                    {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": request.model,
                        "protocol": model.protocol,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
                        "usage": usage.to_dict(),
                        "timing": {"latency_ms": latency_ms, "ttft_ms": first_token_ms},
                        "retries": retry_state["count"],
                        "request_id": request_id,
                        "prompt": prompt_meta,
                    }
                )
                yield b"data: [DONE]\n\n"
            except asyncio.CancelledError:
                record.status = "cancelled"
                record.status_code = 499
                raise
            except GatewayError as exc:
                self._mark_error(record, exc)
                yield self._sse_error(exc, request_id)
                yield b"data: [DONE]\n\n"
            finally:
                record.retries = retry_state["count"]
                record.latency_ms = round((time.perf_counter() - started) * 1000, 2)
                await self._persist(record)

        return generate()

    async def _request_with_retry(
        self,
        adapter: BaseAdapter,
        request: AdapterRequest,
        request_id: str,
        retry_state: dict[str, int],
    ) -> AdapterResult:
        prepared = adapter.prepare(request, request_id)
        last_error: GatewayError | None = None

        for attempt in range(self.config.retry.max_attempts):
            try:
                response = await self.http.post(
                    prepared.url,
                    headers=prepared.headers,
                    json=prepared.payload,
                    timeout=adapter.model.timeout_seconds,
                )
                if response.status_code >= 400:
                    raise map_upstream_http_error(
                        adapter.model.upstream_model,
                        response.status_code,
                        response.text,
                        self.config.retry.retry_statuses,
                        response.headers.get("retry-after"),
                    )
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise UpstreamError(
                        "Upstream returned invalid JSON",
                        status_code=502,
                        error_type="upstream_error",
                        code="upstream_invalid_response",
                        retryable=True,
                    ) from exc
                return adapter.parse_response(payload)
            except httpx.TimeoutException as exc:
                last_error = UpstreamError(
                    f"Upstream {adapter.model.upstream_model} timed out",
                    status_code=504,
                    error_type="upstream_error",
                    code="upstream_timeout",
                    retryable=True,
                )
            except httpx.HTTPError as exc:
                last_error = UpstreamError(
                    f"Upstream {adapter.model.upstream_model} connection failed: {type(exc).__name__}",
                    status_code=502,
                    error_type="upstream_error",
                    code="upstream_connection_error",
                    retryable=True,
                )
            except GatewayError as exc:
                last_error = exc

            if attempt + 1 >= self.config.retry.max_attempts or not last_error.retryable:
                raise last_error
            retry_state["count"] += 1
            await asyncio.sleep(
                backoff_delay(
                    attempt,
                    self.config.retry.base_delay_seconds,
                    self.config.retry.max_delay_seconds,
                )
            )

        if last_error is not None:
            raise last_error
        raise GatewayError("Upstream request failed", status_code=502, code="upstream_error")

    @staticmethod
    def _normalize_stream_error(exc: Exception, model: ModelConfig) -> GatewayError:
        if isinstance(exc, GatewayError):
            return exc
        if isinstance(exc, httpx.TimeoutException):
            return UpstreamError(
                f"Upstream {model.upstream_model} stream timed out",
                status_code=504,
                error_type="upstream_error",
                code="upstream_timeout",
                retryable=True,
            )
        return UpstreamError(
            f"Upstream {model.upstream_model} stream failed: {type(exc).__name__}",
            status_code=502,
            error_type="upstream_error",
            code="upstream_connection_error",
            retryable=True,
        )

    def _new_record(
        self,
        request: ChatRequest,
        request_id: str,
        prompt_meta: dict[str, Any] | None,
    ) -> UsageRecord:
        model = self.model_config(request.model)
        return UsageRecord(
            request_id=request_id,
            model=request.model,
            protocol=model.protocol,
            stream=request.stream,
            prompt_id=prompt_meta["id"] if prompt_meta else None,
            prompt_version=prompt_meta["version"] if prompt_meta else None,
        )

    @staticmethod
    def _accumulate(record: UsageRecord, usage: TokenUsage) -> None:
        record.prompt_tokens += usage.prompt_tokens
        record.completion_tokens += usage.completion_tokens
        record.cached_tokens += usage.cached_tokens
        record.reasoning_tokens += usage.reasoning_tokens

    @staticmethod
    def _mark_error(record: UsageRecord, error: GatewayError) -> None:
        record.status = "error"
        record.status_code = error.status_code
        record.error_code = error.code
        record.error_message = error.message

    async def _persist(self, record: UsageRecord) -> None:
        try:
            await asyncio.to_thread(self.usage.record, record)
        except Exception:
            logger.exception("Failed to persist usage record %s", record.request_id)

    def _completion_payload(
        self,
        request: ChatRequest,
        request_id: str,
        model: ModelConfig,
        record: UsageRecord,
        result: AdapterResult,
        parsed: Any,
        latency_ms: float,
        retries: int,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "protocol": model.protocol,
            "upstream_model": record.upstream_model or model.upstream_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": result.text},
                    "finish_reason": result.finish_reason,
                }
            ],
            "usage": TokenUsage(
                prompt_tokens=record.prompt_tokens,
                completion_tokens=record.completion_tokens,
                cached_tokens=record.cached_tokens,
                reasoning_tokens=record.reasoning_tokens,
            ).to_dict(),
            "timing": {"latency_ms": latency_ms, "ttft_ms": None},
            "retries": retries,
            "request_id": request_id,
            "prompt": {
                "id": record.prompt_id,
                "version": record.prompt_version,
            }
            if record.prompt_id
            else None,
        }
        if parsed is not None:
            payload["structured_output"] = parsed
        return payload

    @staticmethod
    def _sse(payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()

    @staticmethod
    def _sse_error(error: GatewayError, request_id: str) -> bytes:
        data = json.dumps(error.to_dict(request_id), ensure_ascii=False)
        return f"event: error\ndata: {data}\n\n".encode()

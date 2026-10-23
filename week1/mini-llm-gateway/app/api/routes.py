from __future__ import annotations

import secrets
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.errors import GatewayError
from app.schemas import (
    ChatRequest,
    PromptCreate,
    PromptRecord,
    PromptRenderRequest,
)

router = APIRouter()


def request_context(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    if not request_id:
        request_id = f"req_{uuid.uuid4().hex}"
        request.state.request_id = request_id
    return request_id


def authenticate(request: Request) -> None:
    keys = request.app.state.config.api_keys
    if not keys:
        return
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not token or not any(secrets.compare_digest(token, key.get_secret_value()) for key in keys):
        raise GatewayError(
            "Invalid or missing gateway API key",
            status_code=401,
            error_type="authentication_error",
            code="invalid_api_key",
        )


RequestId = Annotated[str, Depends(request_context)]
Auth = Annotated[None, Depends(authenticate)]


@router.post("/v1/chat/completions")
async def chat_completions(
    payload: ChatRequest,
    request: Request,
    request_id: RequestId,
    _: None = Depends(authenticate),
):
    gateway = request.app.state.gateway
    gateway.model_config(payload.model)  # fail fast before consuming a rate-limit token
    await request.app.state.limiter.acquire(payload.model)

    if payload.stream:
        iterator = await gateway.stream(payload, request_id)
        return StreamingResponse(
            iterator,
            media_type="text/event-stream",
            headers={
                "X-Request-ID": request_id,
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )
    result = await gateway.complete(payload, request_id)
    return JSONResponse(result, headers={"X-Request-ID": request_id})


@router.get("/v1/models")
async def list_models(request: Request, _: Auth) -> dict[str, Any]:
    data = []
    for name, model in request.app.state.config.models.items():
        data.append(
            {
                "id": name,
                "object": "model",
                "owned_by": "deepseek",
                "protocol": model.protocol,
                "upstream_model": model.upstream_model,
                "rate_limit": model.rate_limit.model_dump(),
            }
        )
    return {"object": "list", "data": data}


@router.post("/v1/prompts", response_model=PromptRecord, status_code=201)
async def create_prompt(payload: PromptCreate, request: Request, _: Auth) -> PromptRecord:
    return request.app.state.prompts.create_version(payload)


@router.get("/v1/prompts", response_model=list[PromptRecord])
async def list_prompts(request: Request, _: Auth) -> list[PromptRecord]:
    return request.app.state.prompts.list()


@router.get("/v1/prompts/{prompt_id}", response_model=PromptRecord)
async def get_prompt(
    prompt_id: str, request: Request, _: Auth, version: int | None = None
) -> PromptRecord:
    return request.app.state.prompts.get(prompt_id, version)


@router.post("/v1/prompts/{prompt_id}/render")
async def render_prompt(
    prompt_id: str, payload: PromptRenderRequest, request: Request, _: Auth
) -> dict[str, Any]:
    prompt, content = request.app.state.prompts.render(
        prompt_id, payload.variables, payload.version
    )
    return {
        "id": prompt.id,
        "version": prompt.version,
        "role": prompt.role,
        "content": content,
    }


@router.get("/admin/usage")
async def usage(
    request: Request,
    _: Auth,
    limit: int = Query(default=50, ge=1, le=1000),
    model: str | None = None,
) -> dict[str, Any]:
    return {"data": request.app.state.usage.recent(limit, model)}


@router.get("/admin/usage/{request_id}")
async def usage_detail(request_id: str, request: Request, _: Auth) -> dict[str, Any]:
    record = request.app.state.usage.get(request_id)
    if record is None:
        raise GatewayError(
            f"Usage record {request_id!r} was not found",
            status_code=404,
            error_type="invalid_request_error",
            code="usage_not_found",
        )
    return record


@router.get("/admin/metrics")
async def metrics(request: Request, _: Auth) -> dict[str, Any]:
    data = request.app.state.usage.metrics()
    data["rate_limits"] = request.app.state.limiter.snapshot()
    return data


@router.get("/admin/rate-limits")
async def rate_limits(request: Request, _: Auth) -> dict[str, Any]:
    return request.app.state.limiter.snapshot()


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request) -> dict[str, str]:
    return {"status": "ready", "service": request.app.state.config.service_name}

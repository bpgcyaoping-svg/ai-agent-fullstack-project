from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.routes import router
from app.config import GatewayConfig, load_config
from app.core.errors import GatewayError
from app.core.rate_limit import ModelRateLimiter
from app.services.gateway import GatewayService
from app.services.observability import UsageStore
from app.services.prompts import PromptRepository

logger = logging.getLogger(__name__)


def _request_id(request: Request) -> str:
    request_id = getattr(request.state, "request_id", None)
    return request_id or f"req_{uuid.uuid4().hex}"


def create_app(
    config: GatewayConfig | None = None, *, http_client: httpx.AsyncClient | None = None
) -> FastAPI:
    settings = config or load_config()
    owns_client = http_client is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        prompts = PromptRepository(settings.database_path)
        prompts.initialize()
        usage = UsageStore(settings.database_path)
        usage.initialize()
        limiter = ModelRateLimiter(
            {name: model.rate_limit for name, model in settings.models.items()}
        )
        client = http_client or httpx.AsyncClient()

        app.state.config = settings
        app.state.prompts = prompts
        app.state.usage = usage
        app.state.limiter = limiter
        app.state.gateway = GatewayService(settings, client, prompts, usage)
        try:
            yield
        finally:
            if owns_client:
                await client.aclose()

    app = FastAPI(title=settings.service_name, version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request_id)
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error_handler(request: Request, exc: GatewayError) -> JSONResponse:
        return JSONResponse(
            exc.to_dict(_request_id(request)),
            status_code=exc.status_code,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors: list[dict[str, Any]] = exc.errors()
        first = errors[0] if errors else {}
        location = first.get("loc") or []
        param = ".".join(str(part) for part in location[1:]) if len(location) > 1 else None
        message = first.get("msg") or "Request validation failed"
        return JSONResponse(
            {
                "error": {
                    "message": f"Request validation failed: {message}",
                    "type": "invalid_request_error",
                    "code": "invalid_request",
                    "param": param,
                    "request_id": _request_id(request),
                    "details": errors,
                }
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled gateway error")
        return JSONResponse(
            {
                "error": {
                    "message": "Internal gateway error",
                    "type": "internal_error",
                    "code": "internal_error",
                    "param": None,
                    "request_id": _request_id(request),
                }
            },
            status_code=500,
        )

    return app


app = create_app()

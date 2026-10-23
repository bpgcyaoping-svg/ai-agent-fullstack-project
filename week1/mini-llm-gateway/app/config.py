from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

GATEWAY_DIR = Path(__file__).resolve().parent.parent

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def load_dotenv(path: Path, *, override: bool = True) -> None:
    """Minimal .env loader so the gateway has no extra dependency."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or (not override and key in os.environ):
            continue
        os.environ[key] = value


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name, default = match.group(1), match.group(2)
            return os.environ.get(name, default if default is not None else "")

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


class RetryConfig(BaseModel):
    max_attempts: int = Field(default=3, ge=1, le=5)
    base_delay_seconds: float = Field(default=0.5, ge=0, le=30)
    max_delay_seconds: float = Field(default=8, ge=0, le=120)
    retry_statuses: set[int] = Field(default_factory=lambda: {408, 409, 429, 500, 502, 503, 504})


class RateLimitConfig(BaseModel):
    requests_per_minute: int = Field(default=60, ge=1)
    burst: int = Field(default=10, ge=1)


class ModelConfig(BaseModel):
    protocol: Literal["openai_responses", "anthropic_messages"]
    base_url: str
    path: str = ""
    upstream_model: str
    api_key_env: str = "DEEPSEEK_API_KEY"
    anthropic_version: str = "2023-06-01"
    timeout_seconds: float = Field(default=120, gt=0)
    default_max_tokens: int = Field(default=2048, ge=1)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)

    @model_validator(mode="after")
    def normalize(self) -> ModelConfig:
        self.base_url = self.base_url.rstrip("/")
        if not self.path:
            self.path = "/v1/responses" if self.protocol == "openai_responses" else "/v1/messages"
        if not self.path.startswith("/"):
            self.path = f"/{self.path}"
        return self

    @property
    def url(self) -> str:
        return f"{self.base_url}{self.path}"


class GatewayConfig(BaseModel):
    service_name: str = "mini-llm-gateway"
    database_path: str = "data/gateway.db"
    api_keys: list[SecretStr] = Field(default_factory=list)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    structured_repair_attempts: int = Field(default=1, ge=0, le=3)
    models: dict[str, ModelConfig]

    @field_validator("api_keys", mode="before")
    @classmethod
    def drop_blank_keys(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [item for item in value if item]
        return value

    @model_validator(mode="after")
    def validate_models(self) -> GatewayConfig:
        if not self.models:
            raise ValueError("At least one model must be configured")
        return self


def load_config(path: str | Path | None = None) -> GatewayConfig:
    load_dotenv(GATEWAY_DIR / ".env")

    configured = path or os.getenv("GATEWAY_CONFIG") or GATEWAY_DIR / "config.yaml"
    config_path = Path(configured)
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"Gateway config not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    config = GatewayConfig.model_validate(_expand_env(raw))

    database_path = Path(config.database_path)
    if not database_path.is_absolute():
        config.database_path = str((config_path.parent / database_path).resolve())
    return config

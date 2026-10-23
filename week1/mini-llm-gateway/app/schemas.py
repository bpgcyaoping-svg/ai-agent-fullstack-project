from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["system", "user", "assistant"]
    content: str


class PromptReference(BaseModel):
    id: str
    version: int | None = Field(default=None, ge=1)
    variables: dict[str, Any] = Field(default_factory=dict)


class JsonSchemaSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = None
    strict: bool | None = None
    schema_: dict[str, Any] = Field(alias="schema")


class ResponseFormat(BaseModel):
    type: Literal["text", "json_object", "json_schema"] = "text"
    json_schema: JsonSchemaSpec | None = None

    @model_validator(mode="after")
    def require_schema(self) -> ResponseFormat:
        if self.type == "json_schema" and self.json_schema is None:
            raise ValueError("response_format.json_schema is required when type is json_schema")
        return self


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, ge=1)
    response_format: ResponseFormat | None = None
    prompt: PromptReference | None = None
    metadata: dict[str, Any] | None = None


class PromptCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}$")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    role: Literal["system", "user"] = "system"
    content: str = Field(min_length=1)
    activate: bool = True


class PromptRecord(BaseModel):
    id: str
    version: int
    name: str
    description: str | None
    role: str
    content: str
    is_active: bool
    created_at: str


class PromptRenderRequest(BaseModel):
    version: int | None = Field(default=None, ge=1)
    variables: dict[str, Any] = Field(default_factory=dict)


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class Timing(BaseModel):
    latency_ms: float
    ttft_ms: float | None = None

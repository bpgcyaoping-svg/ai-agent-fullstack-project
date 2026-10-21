from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from enum import Enum


class AgentStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class CreateAgentRequest(BaseModel):
    """创建 Agent 的请求体"""
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=50, examples=["CodeReviewer"])
    description: str = Field(default="", max_length=500)
    model: str = Field(default="gpt-4o", examples=["gpt-4o", "deepseek-v3"])
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    tools: list[str] = Field(default_factory=list)
    system_prompt: str = Field(default="", max_length=8000)


class UpdateAgentRequest(BaseModel):
    """更新 Agent —— 所有字段可选（只更新传入的字段）"""
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=50)
    description: str | None = Field(default=None, max_length=500)
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    tools: list[str] | None = None
    system_prompt: str | None = Field(default=None, max_length=8000)
    status: AgentStatus | None = None


class AgentResponse(BaseModel):
    """Agent 的响应体"""
    id: int
    name: str
    description: str
    model: str
    temperature: float
    tools: list[str]
    system_prompt: str
    status: AgentStatus
    created_at: datetime
    updated_at: datetime


class AgentListResponse(BaseModel):
    """Agent 列表响应"""
    total: int
    page: int
    page_size: int
    items: list[AgentResponse]

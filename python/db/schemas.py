from pydantic import BaseModel, Field
from datetime import datetime

class AgentCreate(BaseModel):
    """创建 Agent 的请求体"""
    name: str = Field(min_length=1, max_length=100, description="Agent 名称")
    model: str = Field(default="gpt-4o", description="底层模型")
    system_prompt: str | None = Field(default=None, description="系统提示词")
    temperature: float = Field(default=0.7, ge=0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1, le=128000)

class AgentUpdate(BaseModel):
    """更新 Agent — 所有字段可选（只更新传了的字段）"""
    name: str | None = Field(default=None, min_length=1, max_length=100)
    model: str | None = None
    system_prompt: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1, le=128000)
    is_active: bool | None = None

class AgentResponse(BaseModel):
    """返回给客户端的 Agent 数据"""
    id: int
    name: str
    model: str
    system_prompt: str | None
    temperature: float
    max_tokens: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}  # 允许从 SQLAlchemy 模型直接转换

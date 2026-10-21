from sys import api_version
from pydantic import BaseModel, Field
from typing import Optional


class AgentTask(BaseModel):
    prompt: str = Field(..., min_length=1, description="任务的提示")
    model: str = Field(default="gpt-4o", description="模型的名称")
    temperature: float = Field(default=0.7, ge=0.0, le=1.0,  description="温度参数")

task = AgentTask(
    prompt="写一个排序函数，实现对一个列表的排序"
)
print(f"task: {task}")
dump = task.model_dump()
print(f"dump: {dump}")
json_str = task.model_dump_json()
print(f"json_str: {json_str}")
valid = AgentTask.model_validate(dump)
print(f"valid: {valid}")

task2 = AgentTask.model_validate_json(json_str)
print(f"task2: {task2}")


# 正确做法：default_factory —— 每个实例独立
class GoodModel(BaseModel):
    items: list[str] = Field(default_factory=list)
a = GoodModel()
a.items.append("a")
print(a.items)
b= GoodModel()
b.items.append("b")
print(b.items)


# Filed约束
class LLMRequest(BaseModel):
    prompt: str = Field(..., 
        min_length=1, 
        max_length=1024,
        description="发给模型的提示词"
    )
    model: str = Field(
        default="gpt-4o",
        description="模型的名称"
    )

    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="温度参数"
    )
    max_tokens: int = Field(
        default=1024,
        ge=1,
        description="最大生成token数"
    )
    #正则校验
    api_version: str = Field(
        default="v1",
        pattern=r"^v\d+$",               # 必须是 v + 数字
        description="API 版本号",
    )
    response_format: str = Field(
        default = "text",
        pattern=r"^(text|json)$",         # 只允许 text 或 json
        description="响应格式",
    )
# 超出范围 → 报错
try:
    LLMRequest(prompt="你好", temperature=3.0)
except Exception as e:
    print(e)
    # temperature: Input should be less than or equal to 2

# 太短 → 报错
try:
    LLMRequest(prompt="", max_tokens=0)
except Exception as e:
    print(e)
    # prompt: String should have at least 1 character
    # max_tokens: Input should be greater than 0

# 不符合正则 → 报错
try:
    LLMRequest(prompt="你好", response_format="text")
except Exception as e:
    print(e)


# 嵌套模型
class ToolConfig(BaseModel):
    """工具配置"""
    name: str = Field(..., description="工具名称")
    description: str = Field(..., description="工具描述")
    enabled: bool = Field(default=True)


class MemoryConfig(BaseModel):
    """记忆配置"""
    short_term_size: int = Field(default=10, description="短期记忆条数")
    long_term_enabled: bool = Field(default=False)


class AgentProfile(BaseModel):
    """Agent 完整配置 —— 嵌套模型"""
    name: str
    version: str = "0.1.0"
    model_config: dict = Field(default_factory=dict)     # 也可以用 dict
    tools: list[ToolConfig] = Field(default_factory=list)  # 模型列表！
    memory: MemoryConfig = Field(default_factory=MemoryConfig)  # 嵌套模型

# 创建嵌套实例
agent = AgentProfile(
    name="CodeBuddy",
    version="1.0.0",
    tools=[
        ToolConfig(name="search", description="网络搜索"),
        ToolConfig(name="read_file", description="读取文件"),
        ToolConfig(name="write_file", description="写入文件", enabled=False),
    ],
    memory=MemoryConfig(short_term_size=20, long_term_enabled=True),
)

# 访问嵌套字段
print(agent.tools[1].name)               # read_file
print(agent.memory.short_term_size)       # 20

# 序列化 —— 递归展开
print(agent.model_dump_json(indent=2))
"""Step 3 — 抽象 StreamingModel；Step 4 — 接入模型 Adapter。

Agent Loop 只依赖 StreamingModel 协议与 ModelStreamEvent，
不直接依赖 DeepSeek 的 chunk 结构：
- 换模型 → 新写一个 Adapter；
- 离线测试 → 用 FakeStreamingModel 注入延迟与故障。
（与 1-3 笔记同款分层，这里补充了可注入故障的 Fake 实现。）
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

ModelStreamEventType = Literal["text.delta", "model.finished", "model.usage"]


@dataclass(frozen=True)
class ModelStreamEvent:
    """Adapter 层统一后的模型事件，Loop 只认这三种。"""

    type: ModelStreamEventType
    data: dict[str, Any]


class StreamingModel(Protocol):
    """所有流式模型 Adapter 的统一协议。"""

    name: str

    def stream(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[ModelStreamEvent]: ...


class DeepSeekStreamingModel:
    """DeepSeek（OpenAI 兼容协议）流式 Adapter。"""

    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        max_tokens: int = 1024,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "DeepSeek Adapter 需要 openai 库：请先 pip install openai，"
                "或设置 MODEL_PROVIDER=fake 使用离线模式"
            ) from exc

        self.model = model
        # 重试交给上层失败协议处理，SDK 不偷偷重试
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60.0,
            max_retries=0,
        )
        self.max_tokens = max_tokens

    async def stream(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[ModelStreamEvent]:
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
            max_tokens=self.max_tokens,
            stream_options={"include_usage": True},
            extra_body={"thinking": {"type": "disabled"}},
        )

        async for chunk in response:
            choice = chunk.choices[0] if chunk.choices else None

            if choice and choice.delta.content:
                yield ModelStreamEvent(
                    "text.delta", {"delta": choice.delta.content}
                )

            if choice and choice.finish_reason:
                yield ModelStreamEvent(
                    "model.finished",
                    {"finish_reason": choice.finish_reason},
                )

            if chunk.usage is not None:
                yield ModelStreamEvent(
                    "model.usage",
                    {
                        "input_tokens": chunk.usage.prompt_tokens,
                        "output_tokens": chunk.usage.completion_tokens,
                        "total_tokens": chunk.usage.total_tokens,
                    },
                )


class FakeStreamingModel:
    """离线测试用 Fake 模型：确定性输出，可注入延迟与故障。

    - fail_after: 第 N 个 delta 之后抛出 error（测失败协议）
    - error: 要抛出的异常，缺省为 RuntimeError
    - finish_reason="length" 可模拟输出截断
    - delay: 每个 delta 之间的停顿（测取消 / TTFT）
    """

    name = "fake"

    def __init__(
        self,
        text: str = "你好，这是 Fake 模型的流式输出，用于离线测试网关。",
        *,
        chunk_size: int = 4,
        delay: float = 0.02,
        finish_reason: str = "stop",
        usage: dict[str, int] | None = None,
        fail_after: int | None = None,
        error: Exception | None = None,
    ) -> None:
        self.text = text
        self.chunk_size = chunk_size
        self.delay = delay
        self.finish_reason = finish_reason
        output_tokens = max(1, len(text) // 2)
        self.usage = usage or {
            "input_tokens": 21,
            "output_tokens": output_tokens,
            "total_tokens": 21 + output_tokens,
        }
        self.fail_after = fail_after
        self.error = error

    async def stream(
        self, messages: list[dict[str, str]]
    ) -> AsyncIterator[ModelStreamEvent]:
        deltas = [
            self.text[i : i + self.chunk_size]
            for i in range(0, len(self.text), self.chunk_size)
        ]

        for index, delta in enumerate(deltas):
            if self.fail_after is not None and index >= self.fail_after:
                raise self.error or RuntimeError("模拟模型流中途失败")
            if self.delay > 0:
                await asyncio.sleep(self.delay)
            yield ModelStreamEvent("text.delta", {"delta": delta})

        yield ModelStreamEvent(
            "model.finished", {"finish_reason": self.finish_reason}
        )
        yield ModelStreamEvent("model.usage", dict(self.usage))


def build_model() -> StreamingModel:
    """根据环境变量构建模型实例：MODEL_PROVIDER=fake 时离线运行。"""
    provider = os.getenv("MODEL_PROVIDER", "deepseek")
    if provider == "fake":
        return FakeStreamingModel()
    if provider == "deepseek":
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError(
                "请先设置环境变量 DEEPSEEK_API_KEY，或设置 MODEL_PROVIDER=fake 离线运行"
            )
        return DeepSeekStreamingModel(
            api_key=api_key,
            model=os.getenv("GATEWAY_MODEL", "deepseek-chat"),
            base_url=os.getenv("GATEWAY_BASE_URL", "https://api.deepseek.com"),
        )
    raise ValueError(f"未知 MODEL_PROVIDER: {provider}")

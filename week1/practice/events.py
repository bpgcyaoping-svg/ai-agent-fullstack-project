"""Step 1 — 定义统一的 Harness Event（RunEvent）。

这是整个网关"可观察 / 可恢复 / 可审计"的地基：
- 一次 Run 的所有活动（启动、流式增量、用量、失败、取消、检查点）
  都落成同一种结构 RunEvent，seq 在 Run 内从 0 单调递增。
- 订阅端拿 seq 做断线续传游标（SSE Last-Event-ID）→ 可恢复。
- 审计端按 run_id 聚合事件即得到完整 Trace → 可审计。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


def now_ms() -> int:
    """事件时间戳（epoch 毫秒），审计时用它还原事件时间线。"""
    return int(time.time() * 1000)


EventType = Literal[
    "run.started",       # Run 开始执行
    "turn.started",      # 一个回合开始
    "model.started",     # 发起模型流式调用
    "text.delta",        # 模型输出的增量文本
    "model.finished",    # 模型流正常收尾（携带 finish_reason）
    "model.usage",       # token 用量
    "checkpoint.saved",  # CheckPoint 已落库（审计检查点节奏）
    "run.completed",     # 终态：成功
    "run.failed",        # 终态：失败（携带失败协议字段）
    "run.cancelled",     # 终态：显式取消
]

# 终态事件：订阅端看到其中之一即可停止等待；一个 Run 只允许一个终态。
TERMINAL_EVENT_TYPES: frozenset[str] = frozenset(
    {"run.completed", "run.failed", "run.cancelled"}
)


@dataclass(frozen=True)
class RunEvent:
    """统一的 Harness Event。

    字段设计对应三大能力：
    - run_id: 一次独立 Run 的唯一标识，也是 Trace 的聚合键（可审计）
    - seq:    Run 内从 0 单调递增，SSE 的 id / 断线续传游标（可恢复）
    - type:   统一事件词表，屏蔽底层模型差异（可观察）
    - data:   事件负载（增量文本 / usage / 失败协议字段等）
    - ts:     服务端记录事件的时间戳（可审计）
    """

    run_id: str
    seq: int
    type: EventType
    data: dict[str, Any] = field(default_factory=dict)
    ts: int = field(default_factory=now_ms)

    def is_terminal(self) -> bool:
        return self.type in TERMINAL_EVENT_TYPES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

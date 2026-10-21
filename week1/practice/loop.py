"""Step 6 — Agent Loop：RunState + execute_run。

一个 Run 的主循环职责（对应四大能力）：
1. 落 run.started / turn.started / model.started        （可观察）
2. 消费 ModelStreamEvent → 转换为统一 RunEvent          （Harness Event）
3. 记录 TTFT：首个"非空"text.delta 到达时刻 - run 开始时刻
4. 响应两种取消：显式 cancel_event（用户请求）与任务级 CancelledError（可取消）
5. 异常统一走失败协议 → run.failed{code, retryable, ...}（可审计）
6. 周期性与终态保存 CheckPoint 落库                     （可恢复）
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Optional

from errors import normalize_exception
from events import EventType, RunEvent
from model import StreamingModel
from store import CheckPoint, RunStore

CHECKPOINT_EVERY = 25  # 每 25 个事件保存一次中间 CheckPoint


@dataclass
class StreamMetrics:
    """TTFT 统计（1-3 笔记 latency.py 同款契约）。

    TTFT 必须等第一个"非空" text.delta 才开始计时——
    首个供应商事件可能只是空 delta 或心跳，用户感知不到。
    """

    started_at: float
    first_event_at: Optional[float] = None
    first_token_at: Optional[float] = None
    completed_at: Optional[float] = None

    def mark_first_event(self) -> None:
        if self.first_event_at is None:
            self.first_event_at = perf_counter()

    def mark_text_delta(self, text_delta: str | None) -> None:
        self.mark_first_event()
        if self.first_token_at is None and text_delta:
            self.first_token_at = perf_counter()

    def mark_completed(self) -> None:
        if self.first_event_at is None:
            self.mark_first_event()
        self.completed_at = perf_counter()

    def snapshot(self) -> dict[str, Optional[float]]:
        return {
            "first_event_seconds": (
                None
                if self.first_event_at is None
                else self.first_event_at - self.started_at
            ),
            "ttft_seconds": (
                None
                if self.first_token_at is None
                else self.first_token_at - self.started_at
            ),
            "generation_seconds": (
                None
                if self.first_token_at is None or self.completed_at is None
                else self.completed_at - self.first_token_at
            ),
            "total_seconds": (
                None
                if self.completed_at is None
                else self.completed_at - self.started_at
            ),
        }


@dataclass
class RunState:
    """一个 Run 的运行时状态：事件缓存 + 订阅通知 + 取消信号。

    append() 是唯一的事件入口：
    分配 seq → 写内存缓存 → 落库 → 唤醒所有 SSE 订阅者。
    """

    run_id: str
    store: RunStore
    status: str = "created"
    next_seq: int = 0
    events: list[RunEvent] = field(default_factory=list)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)

    # 执行期临时状态
    text_parts: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    metrics: StreamMetrics | None = None

    async def append(self, event_type: EventType, data: dict[str, Any]) -> RunEvent:
        async with self.changed:
            event = RunEvent(
                run_id=self.run_id,
                seq=self.next_seq,
                type=event_type,
                data=data,
            )
            self.next_seq += 1
            self.events.append(event)
            self.store.append_event(event)  # 同步落库，保证 Replay/审计数据完整
            self.changed.notify_all()
            return event


def load_state_from_store(run_id: str, store: RunStore) -> RunState | None:
    """服务重启后从库里还原 RunState（Replay-only：事件只读，执行循环已不在）。"""
    record = store.get_run(run_id)
    if record is None:
        return None
    state = RunState(run_id=run_id, store=store)
    state.events = store.get_events(run_id, -1)
    state.next_seq = state.events[-1].seq + 1 if state.events else 0
    state.status = record.status
    return state


async def _save_checkpoint(state: RunState, *, announce: bool) -> None:
    """保存 CheckPoint；announce=True 时追加一条 checkpoint.saved 事件供审计。"""
    checkpoint = CheckPoint(
        run_id=state.run_id,
        status=state.status,
        next_seq=state.next_seq,
        partial_text="".join(state.text_parts),
        usage=dict(state.usage),
        metrics=state.metrics.snapshot() if state.metrics else None,
    )
    state.store.save_checkpoint(checkpoint)
    if announce:
        await state.append(
            "checkpoint.saved",
            {"next_seq": checkpoint.next_seq, "status": checkpoint.status},
        )


async def _finish_cancelled(state: RunState, reason: str) -> None:
    """落 run.cancelled 终态；保证一个 Run 只有一个终态事件。"""
    if state.events and state.events[-1].is_terminal():
        state.status = state.events[-1].type.split(".")[-1]
        return
    if state.metrics:
        state.metrics.mark_completed()
    state.status = "cancelled"
    state.store.update_run(
        state.run_id,
        status="cancelled",
        metrics=state.metrics.snapshot() if state.metrics else None,
    )
    await _save_checkpoint(state, announce=False)
    await state.append(
        "run.cancelled",
        {"reason": reason, "partial_text": "".join(state.text_parts)},
    )


async def _finish_failed(
    state: RunState,
    *,
    code: str,
    retryable: bool,
    message: str,
    status_code: int | None = None,
    request_id: str | None = None,
) -> None:
    """失败协议落盘：run.failed 携带统一失败码，供客户端与审计系统消费。"""
    if state.metrics:
        state.metrics.mark_completed()
    state.status = "failed"
    state.store.update_run(
        state.run_id,
        status="failed",
        metrics=state.metrics.snapshot() if state.metrics else None,
    )
    await _save_checkpoint(state, announce=False)
    await state.append(
        "run.failed",
        {
            "code": code,
            "retryable": retryable,
            "message": message,
            "status_code": status_code,
            "request_id": request_id,
            "partial_text": "".join(state.text_parts),
        },
    )


async def execute_run(
    state: RunState,
    model: StreamingModel,
    messages: list[dict[str, str]],
    *,
    checkpoint_every: int = CHECKPOINT_EVERY,
) -> None:
    """Agent Loop 主体：模型流 → 统一事件流。"""
    state.status = "running"
    state.metrics = StreamMetrics(started_at=perf_counter())
    state.store.update_run(state.run_id, status="running")

    await state.append("run.started", {"status": "running", "model": model.name})
    await state.append("turn.started", {"turn": 1})
    await state.append(
        "model.started",
        {"model": model.name, "message_count": len(messages)},
    )

    finish_reason: str | None = None

    try:
        async for model_event in model.stream(messages):
            # 显式取消（用户请求）：下一个模型事件到达时立即终止
            if state.cancel_event.is_set():
                await _finish_cancelled(state, reason="user_requested")
                return

            if model_event.type == "text.delta":
                delta = model_event.data["delta"]
                state.text_parts.append(delta)
                state.metrics.mark_text_delta(delta)  # TTFT 统计点
                await state.append("text.delta", {"delta": delta})
            elif model_event.type == "model.finished":
                finish_reason = model_event.data["finish_reason"]
                await state.append(
                    "model.finished", {"finish_reason": finish_reason}
                )
            elif model_event.type == "model.usage":
                state.usage = dict(model_event.data)
                await state.append("model.usage", {"usage": state.usage})

            if state.next_seq % checkpoint_every == 0:
                await _save_checkpoint(state, announce=True)

        # —— 流正常结束 ——
        if finish_reason == "length":
            # 失败协议：输出被截断视为失败，让上层决定如何续写
            await _finish_failed(
                state,
                code="output_truncated",
                retryable=False,
                message="输出因 max_tokens 被截断",
            )
            return

        if state.metrics:
            state.metrics.mark_completed()
        state.status = "completed"
        state.store.update_run(
            state.run_id,
            status="completed",
            metrics=state.metrics.snapshot() if state.metrics else None,
        )
        await _save_checkpoint(state, announce=False)
        await state.append(
            "run.completed",
            {
                "text": "".join(state.text_parts),
                "usage": dict(state.usage),
                "finish_reason": finish_reason,
                "metrics": state.metrics.snapshot() if state.metrics else None,
            },
        )
    except asyncio.CancelledError:
        # 任务级取消：网关在显式取消接口里 task.cancel()
        await _finish_cancelled(state, reason="task_cancelled")
        raise
    except Exception as exc:
        error = normalize_exception(exc)
        await _finish_failed(
            state,
            code=error.code,
            retryable=error.retryable,
            message=error.message,
            status_code=error.status_code,
            request_id=error.request_id,
        )

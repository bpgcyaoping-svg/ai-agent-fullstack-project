"""Step 9 / Step 11 — 异常测试 + 功能测试（离线可跑，Fake 模型，无需 API Key）。

运行：python tests.py
覆盖：
    1.  SSE 编解码 roundtrip
    2.  正常生命周期 + TTFT 统计
    3.  用户显式取消（cancel_event）
    4.  任务级取消（task.cancel → CancelledError）
    5.  失败协议（限流异常 / 通用异常 → run.failed{code, retryable}）
    6.  输出截断（finish_reason=length → output_truncated）
    7.  断线重连 Replay（after_seq 游标只收增量）
    8.  CheckPoint 落库 + 服务重启后可恢复
    9.  终态幂等（重复取消不产生第二个终态）
    10. 网关 HTTP 冒烟（TestClient：创建 → SSE → Trace）
"""

# 必须在导入 gateway 之前设置环境变量（gateway 在 import 时读取）
import os
import tempfile

_TESTS_TMPDIR = tempfile.mkdtemp(prefix="gateway_practice_")
os.environ.setdefault("MODEL_PROVIDER", "fake")
os.environ.setdefault("GATEWAY_DB", os.path.join(_TESTS_TMPDIR, "smoke_runs.db"))

import asyncio  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402

from errors import ModelCallError  # noqa: E402
from events import RunEvent, now_ms  # noqa: E402
from loop import RunState, execute_run, load_state_from_store  # noqa: E402
from model import FakeStreamingModel  # noqa: E402
from store import InMemoryRunStore, RunRecord, SqliteRunStore  # noqa: E402
from sse import SSEDecoder, encode_sse, parse_sse_frame  # noqa: E402

MESSAGES = [
    {"role": "system", "content": "测试系统提示"},
    {"role": "user", "content": "测试提示词"},
]

TESTS: list = []


def test(func):
    TESTS.append(func)
    return func


def make_state(store, prompt: str = "测试提示词") -> RunState:
    run_id = f"run_test_{now_ms()}_{id(store) & 0xFFFF}"
    store.create_run(RunRecord(run_id=run_id, prompt=prompt, status="running"))
    return RunState(run_id=run_id, store=store)


async def wait_for_event(state: RunState, event_type: str, timeout: float = 5.0):
    async def _wait():
        while True:
            for e in state.events:
                if e.type == event_type:
                    return e
            await asyncio.sleep(0.005)

    return await asyncio.wait_for(_wait(), timeout)


# ---------- 1. SSE 编解码 ----------


@test
async def test_sse_roundtrip():
    store = InMemoryRunStore()
    state = make_state(store)
    event = RunEvent(
        run_id=state.run_id, seq=7, type="text.delta", data={"delta": "你好"}
    )

    frame_text = encode_sse(event)
    lines = [line for line in frame_text.split("\n") if line != ""]
    frame = parse_sse_frame(lines)

    assert frame.id == "7", f"id 行应承载 seq，实际 {frame.id!r}"
    assert frame.event == "text.delta"
    payload = json.loads(frame.data)
    assert payload["seq"] == 7
    assert payload["data"]["delta"] == "你好"

    # 流式解码器：逐行喂入，空行切帧
    decoder = SSEDecoder()
    got = None
    for line in frame_text.splitlines():
        got = decoder.feed_line(line) or got
    assert got is not None and got.event == "text.delta"

    # 心跳注释帧应被忽略（feed 后不产生帧）
    decoder2 = SSEDecoder()
    assert decoder2.feed_line(": keep-alive") is None
    assert decoder2.feed_line("") is None


# ---------- 2. 生命周期 + TTFT ----------


@test
async def test_lifecycle_and_ttft():
    store = InMemoryRunStore()
    state = make_state(store)
    model = FakeStreamingModel(text="流式输出测试文本", delay=0.005)

    task = asyncio.create_task(execute_run(state, model, MESSAGES))
    completed = await wait_for_event(state, "run.completed")
    await task

    types = [e.type for e in state.events]
    assert types[0] == "run.started"
    assert "turn.started" in types and "model.started" in types
    assert "text.delta" in types
    assert types.count("run.completed") == 1
    assert types[-1] == "run.completed", "终态事件必须是最后一个"

    metrics = completed.data["metrics"]
    assert metrics is not None
    assert metrics["ttft_seconds"] is not None and metrics["ttft_seconds"] >= 0
    assert metrics["total_seconds"] is not None

    # 全量事件应与库中一致（审计 Trace）
    assert len(store.get_events(state.run_id, -1)) == len(state.events)
    assert store.get_run(state.run_id).status == "completed"


# ---------- 3. 用户显式取消 ----------


@test
async def test_user_cancel():
    store = InMemoryRunStore()
    state = make_state(store)
    model = FakeStreamingModel(text="这一段输出足够长以便有时间触发取消操作", delay=0.03)

    task = asyncio.create_task(execute_run(state, model, MESSAGES))
    await wait_for_event(state, "text.delta")
    state.cancel_event.set()  # 显式取消（不 task.cancel）

    cancelled = await wait_for_event(state, "run.cancelled")
    await asyncio.wait_for(task, timeout=5)

    assert cancelled.data["reason"] == "user_requested"
    assert cancelled.data["partial_text"], "已收到的增量文本应保留在事件里"

    terminal = [e for e in state.events if e.is_terminal()]
    assert len(terminal) == 1 and terminal[0].type == "run.cancelled"
    assert store.get_run(state.run_id).status == "cancelled"

    checkpoint = store.get_checkpoint(state.run_id)
    assert checkpoint is not None and checkpoint.status == "cancelled"


# ---------- 4. 任务级取消 ----------


@test
async def test_task_cancel():
    store = InMemoryRunStore()
    state = make_state(store)
    model = FakeStreamingModel(text="任务级取消测试文本", delay=0.03)

    task = asyncio.create_task(execute_run(state, model, MESSAGES))
    await wait_for_event(state, "text.delta")
    task.cancel()

    cancelled = await wait_for_event(state, "run.cancelled")
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert cancelled.data["reason"] == "task_cancelled"
    assert store.get_run(state.run_id).status == "cancelled"


# ---------- 5. 失败协议 ----------


@test
async def test_failure_protocol_rate_limited():
    store = InMemoryRunStore()
    state = make_state(store)
    model = FakeStreamingModel(
        fail_after=2,
        error=ModelCallError("rate_limited", "429 Too Many Requests", True, 429),
    )

    task = asyncio.create_task(execute_run(state, model, MESSAGES))
    failed = await wait_for_event(state, "run.failed")
    await task

    assert failed.data["code"] == "rate_limited"
    assert failed.data["retryable"] is True
    assert failed.data["status_code"] == 429
    assert failed.data["partial_text"], "失败前已生成的增量应保留"
    assert store.get_run(state.run_id).status == "failed"


@test
async def test_failure_protocol_generic_error():
    store = InMemoryRunStore()
    state = make_state(store)
    model = FakeStreamingModel(fail_after=1)  # 默认 RuntimeError

    task = asyncio.create_task(execute_run(state, model, MESSAGES))
    failed = await wait_for_event(state, "run.failed")
    await task

    assert failed.data["code"] == "model_stream_failed"
    assert failed.data["retryable"] is True


# ---------- 6. 输出截断 ----------


@test
async def test_output_truncated():
    store = InMemoryRunStore()
    state = make_state(store)
    model = FakeStreamingModel(finish_reason="length")

    task = asyncio.create_task(execute_run(state, model, MESSAGES))
    failed = await wait_for_event(state, "run.failed")
    await task

    assert failed.data["code"] == "output_truncated"
    assert failed.data["retryable"] is False
    assert store.get_run(state.run_id).status == "failed"


# ---------- 7. 断线重连 Replay ----------


@test
async def test_replay_after_reconnect():
    store = InMemoryRunStore()
    state = make_state(store)
    await execute_run(state, FakeStreamingModel(), MESSAGES)

    all_events = state.events
    assert all_events[-1].is_terminal()

    # 模拟断线重连：客户端 Last-Event-ID=2，服务端只补发 seq>2 的事件
    replayed = store.get_events(state.run_id, after_seq=2)
    assert replayed[0].seq == 3
    assert replayed[-1].is_terminal()
    assert len(replayed) == len(all_events) - 3


# ---------- 8. CheckPoint 落库 + 重启恢复 ----------


@test
async def test_checkpoint_survives_restart():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "runs.db"
        store = SqliteRunStore(db_path)
        try:
            state = make_state(store)
            await execute_run(state, FakeStreamingModel(), MESSAGES)
            event_count = len(state.events)
        finally:
            store.close()

        # 模拟服务重启：用同一个库文件新建 store 实例
        restarted_store = SqliteRunStore(db_path)
        try:
            restored = load_state_from_store(state.run_id, restarted_store)
            assert restored is not None
            assert restored.status == "completed"
            assert len(restored.events) == event_count
            assert restored.events[-1].type == "run.completed"

            checkpoint = restarted_store.get_checkpoint(state.run_id)
            assert checkpoint is not None
            assert checkpoint.status == "completed"
            assert checkpoint.partial_text != ""
        finally:
            restarted_store.close()


# ---------- 9. 终态幂等 ----------


@test
async def test_terminal_idempotent():
    store = InMemoryRunStore()
    state = make_state(store)
    model = FakeStreamingModel(text="终态幂等测试文本", delay=0.02)

    task = asyncio.create_task(execute_run(state, model, MESSAGES))
    await wait_for_event(state, "text.delta")
    state.cancel_event.set()
    await wait_for_event(state, "run.cancelled")
    state.cancel_event.set()  # 重复取消
    await asyncio.wait_for(task, timeout=5)

    terminal = [e for e in state.events if e.is_terminal()]
    assert len(terminal) == 1


# ---------- 10. 网关 HTTP 冒烟（TestClient） ----------


@test
async def test_gateway_smoke():
    from fastapi.testclient import TestClient

    from gateway import app

    with TestClient(app) as client:
        # 创建 Run
        resp = client.post("/v1/runs", json={"prompt": "冒烟测试提示词"})
        assert resp.status_code == 200, resp.text
        run_id = resp.json()["run_id"]

        # SSE 订阅直至终态
        terminal_event = None
        event_count = 0
        with client.stream(
            "GET", f"/v1/runs/{run_id}/events", params={"after_seq": -1}
        ) as stream:
            assert stream.status_code == 200
            decoder = SSEDecoder()
            for line in stream.iter_lines():
                frame = decoder.feed_line(line)
                if frame is None or not frame.data:
                    continue
                event = json.loads(frame.data)
                event_count += 1
                assert frame.id == str(event["seq"]), "SSE id 应等于 seq"
                if event["type"] == "run.completed":
                    terminal_event = event
                    break

        assert terminal_event is not None, "应收到 run.completed 终态"
        assert terminal_event["data"]["metrics"]["ttft_seconds"] is not None

        # 审计接口：Trace + 详情（含 CheckPoint）
        trace = client.get(f"/v1/runs/{run_id}/trace").json()
        assert trace["event_count"] == event_count
        detail = client.get(f"/v1/runs/{run_id}").json()
        assert detail["run"]["status"] == "completed"
        assert detail["checkpoint"]["status"] == "completed"

        # 断线重连语义：after_seq=2 只补发增量
        replay_resp = client.get(
            f"/v1/runs/{run_id}/events", params={"after_seq": 2}
        )
        assert replay_resp.status_code == 200
        replay_text = replay_resp.text
        assert "id: 3" in replay_text, "Replay 应从 seq=3 开始补发"


# ---------- 运行器 ----------


async def main() -> None:
    passed = 0
    failures: list[tuple[str, Exception]] = []

    for func in TESTS:
        try:
            await func()
            passed += 1
            print(f"[PASS] {func.__name__}")
        except Exception as exc:
            failures.append((func.__name__, exc))
            print(f"[FAIL] {func.__name__}: {exc!r}")

    total = len(TESTS)
    print(f"\nPass rate: {passed}/{total}")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())

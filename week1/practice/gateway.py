"""Step 7 — Streaming Agent Gateway（FastAPI 网关）。

接口一览：
    POST /v1/runs                创建独立 Run
    GET  /v1/runs                Run 列表（审计）
    GET  /v1/runs/{id}           Run 详情 + CheckPoint（审计）
    GET  /v1/runs/{id}/trace     完整事件 Trace（审计）
    GET  /v1/runs/{id}/events    SSE 订阅（Last-Event-ID / after_seq 续传，可恢复）
    POST /v1/runs/{id}/cancel    显式取消（可取消）
    GET  /                       网页客户端（Step 8）

离线体验：MODEL_PROVIDER=fake python gateway.py
真实模型：set DEEPSEEK_API_KEY=... && python gateway.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

from loop import RunState, execute_run, load_state_from_store
from model import StreamingModel, build_model
from store import RunRecord, SqliteRunStore
from sse import encode_heartbeat, encode_sse

DB_PATH = os.getenv("GATEWAY_DB", "gateway_runs.db")
SYSTEM_PROMPT = "你是代码审查助手，只报告有证据的问题。"

store = SqliteRunStore(DB_PATH)
RUNS: dict[str, RunState] = {}
RUN_TASKS: dict[str, asyncio.Task] = {}

_model_instance: StreamingModel | None = None


def get_model() -> StreamingModel:
    global _model_instance
    if _model_instance is None:
        _model_instance = build_model()
    return _model_instance


class CreateRunRequest(BaseModel):
    prompt: str = Field(min_length=1, description="发送给模型的用户提示词")


app = FastAPI(title="Streaming Agent Gateway (practice)")


def _record_to_dict(record: RunRecord) -> dict:
    return asdict(record)


# ---------- 创建 Run ----------


@app.post("/v1/runs")
async def create_run(body: CreateRunRequest) -> dict[str, str]:
    try:
        model = get_model()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    run_id = f"run_{uuid.uuid4().hex}"
    record = RunRecord(run_id=run_id, prompt=body.prompt, status="running")
    store.create_run(record)

    state = RunState(run_id=run_id, store=store)
    RUNS[run_id] = state

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": body.prompt},
    ]
    task = asyncio.create_task(execute_run(state, model, messages))
    RUN_TASKS[run_id] = task
    task.add_done_callback(lambda _: RUN_TASKS.pop(run_id, None))

    return {"run_id": run_id, "status": state.status}


# ---------- SSE 订阅（Replay + 断线续传） ----------


async def subscribe(
    request: Request,
    state: RunState,
    after_seq: int,
    *,
    live: bool,
) -> AsyncIterator[str]:
    """先补发 after_seq 之后的历史事件（Replay），再跟随实时事件。

    - 断线重连：客户端带上次收到的最大 seq，只收增量 → 可恢复
    - 心跳：15 秒无事件时发注释帧，防止代理断开空闲连接
    - live=False（服务重启后）：只做 Replay，事件补发完即结束
    """
    cursor = after_seq + 1

    while True:
        while cursor < len(state.events):
            event = state.events[cursor]
            cursor += 1
            yield encode_sse(event)
            if event.is_terminal():
                return

        if await request.is_disconnected():
            return

        if not live:
            return

        try:
            async with state.changed:
                await asyncio.wait_for(state.changed.wait(), timeout=15.0)
        except TimeoutError:
            yield encode_heartbeat()


@app.get("/v1/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    after_seq: int = -1,
) -> StreamingResponse:
    state = RUNS.get(run_id)
    live = state is not None
    if state is None:
        state = load_state_from_store(run_id, store)
    if state is None:
        raise HTTPException(status_code=404, detail="run not found")

    raw_after = last_event_id if last_event_id is not None else after_seq
    try:
        resolved_after = int(raw_after)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="invalid Last-Event-ID / after_seq"
        ) from exc

    return StreamingResponse(
        subscribe(request, state, resolved_after, live=live),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------- 显式取消 ----------


@app.post("/v1/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict[str, str]:
    state = RUNS.get(run_id)
    if state is not None:
        if state.status in {"completed", "failed", "cancelled"}:
            # 终态幂等：重复取消不产生第二个终态
            return {"run_id": run_id, "status": state.status}
        state.cancel_event.set()
        task = RUN_TASKS.get(run_id)
        if task is not None:
            task.cancel()
        return {"run_id": run_id, "status": "cancelling"}

    record = store.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    # 服务重启等场景：Run 已不在内存，返回库里的最终状态（幂等）
    return {"run_id": run_id, "status": record.status}


# ---------- 审计接口 ----------


@app.get("/v1/runs")
async def list_runs() -> list[dict]:
    return [_record_to_dict(r) for r in store.list_runs()]


@app.get("/v1/runs/{run_id}")
async def run_detail(run_id: str) -> dict:
    record = store.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="run not found")
    checkpoint = store.get_checkpoint(run_id)
    return {
        "run": _record_to_dict(record),
        "checkpoint": checkpoint.to_dict() if checkpoint else None,
    }


@app.get("/v1/runs/{run_id}/trace")
async def run_trace(run_id: str) -> dict:
    if store.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = store.get_events(run_id, -1)
    return {
        "run_id": run_id,
        "event_count": len(events),
        "events": [e.to_dict() for e in events],
    }


# ---------- 网页客户端（Step 8） ----------

HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Streaming Agent Gateway</title>
<style>
  body { font-family: -apple-system, "Segoe UI", sans-serif; margin: 0; padding: 20px; background: #f5f7fb; color: #1f2937; }
  .wrap { max-width: 960px; margin: 0 auto; display: grid; gap: 14px; }
  .card { background: #fff; border: 1px solid #dbe3f0; border-radius: 14px; padding: 18px; }
  h2, h3 { margin-top: 0; }
  textarea { width: 100%; min-height: 72px; padding: 10px; border: 1px solid #cbd5e1; border-radius: 10px; font: inherit; box-sizing: border-box; resize: vertical; }
  button { border: 0; border-radius: 999px; padding: 9px 14px; margin: 8px 8px 0 0; cursor: pointer; background: #2563eb; color: #fff; font: inherit; }
  button.ghost { background: #e2e8f0; color: #0f172a; }
  button.danger { background: #dc2626; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .meta { display: flex; gap: 18px; flex-wrap: wrap; font-size: 14px; color: #475569; margin-top: 10px; }
  .meta b { color: #0f172a; }
  #text { min-height: 80px; white-space: pre-wrap; background: #f8fafc; border: 1px solid #dbe3f0; border-radius: 10px; padding: 12px; }
  #metrics { font-size: 14px; color: #475569; }
  pre { background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 10px; max-height: 280px; overflow: auto; font-size: 12px; white-space: pre-wrap; }
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <h2>Streaming Agent Gateway — 练习演示</h2>
    <textarea id="prompt">请用三句话解释 SSE 的 Last-Event-ID 如何实现断线续传</textarea>
    <div>
      <button id="btnCreate">创建 Run 并订阅</button>
      <button id="btnReconnect" class="ghost" disabled>按游标重连 (Replay)</button>
      <button id="btnDisconnect" class="ghost" disabled>断开订阅</button>
      <button id="btnCancel" class="danger" disabled>取消 Run</button>
    </div>
    <div class="meta">
      <span>Run ID: <b id="runId">-</b></span>
      <span>状态: <b id="status">未连接</b></span>
      <span>游标 seq: <b id="lastSeq">-</b></span>
    </div>
  </div>
  <div class="card">
    <h3>模型输出</h3>
    <div id="text">等待输出…</div>
    <p id="metrics"></p>
  </div>
  <div class="card">
    <h3>事件 Trace（可审计）</h3>
    <pre id="log">等待事件…</pre>
  </div>
</div>
<script>
const $ = id => document.getElementById(id);
let runId = null, es = null, lastSeq = -1, parts = [];
const EVENT_TYPES = ["run.started","turn.started","model.started","text.delta",
  "model.finished","model.usage","checkpoint.saved","run.completed","run.failed","run.cancelled"];

function log(line) {
  const el = $("log");
  el.textContent = (el.textContent === "等待事件…" ? "" : el.textContent) + line + "\\n";
  el.scrollTop = el.scrollHeight;
}
function setStatus(t) { $("status").textContent = t; }
function refreshButtons() {
  $("btnReconnect").disabled = !runId || !!es;
  $("btnDisconnect").disabled = !es;
  $("btnCancel").disabled = !runId;
}
function handleEvent(p) {
  lastSeq = p.seq;
  $("lastSeq").textContent = p.seq;
  const body = p.type === "text.delta" ? JSON.stringify(p.data.delta) : JSON.stringify(p.data);
  log("#" + p.seq + " " + p.type + " " + body);
  if (p.type === "text.delta") {
    parts.push(p.data.delta);
    $("text").textContent = parts.join("");
  }
  if (p.type === "run.completed") {
    const m = p.data.metrics || {};
    $("metrics").textContent = "TTFT: " + (m.ttft_seconds == null ? "-" : m.ttft_seconds.toFixed(3))
      + "s | 总耗时: " + (m.total_seconds == null ? "-" : m.total_seconds.toFixed(3))
      + "s | tokens: " + JSON.stringify(p.data.usage || {});
  }
  if (p.type === "run.completed" || p.type === "run.failed" || p.type === "run.cancelled") {
    setStatus(p.type);
    closeEs();
  }
}
function subscribe(id, after) {
  closeEs();
  runId = id; $("runId").textContent = id;
  es = new EventSource("/v1/runs/" + id + "/events?after_seq=" + after);
  setStatus(after >= 0 ? "重连中(Replay)…" : "连接中…");
  es.onopen = () => setStatus("已连接");
  es.onerror = () => { setStatus("连接断开（可按游标重连）"); closeEs(); };
  for (const t of EVENT_TYPES) {
    es.addEventListener(t, ev => handleEvent(JSON.parse(ev.data)));
  }
  refreshButtons();
}
function closeEs() { if (es) { es.close(); es = null; } refreshButtons(); }

$("btnCreate").onclick = async () => {
  parts = []; lastSeq = -1;
  $("text").textContent = "等待输出…"; $("log").textContent = "等待事件…";
  $("metrics").textContent = ""; $("lastSeq").textContent = "-";
  try {
    const resp = await fetch("/v1/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: $("prompt").value }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(JSON.stringify(data));
    log("create_run → " + JSON.stringify(data));
    subscribe(data.run_id, -1);
  } catch (e) { log("create_run_error → " + e); }
};
$("btnReconnect").onclick = () => { if (runId) subscribe(runId, lastSeq); };
$("btnDisconnect").onclick = () => { log("manual_disconnect：只断订阅，不取消 Run"); closeEs(); };
$("btnCancel").onclick = async () => {
  if (!runId) return;
  try {
    const resp = await fetch("/v1/runs/" + runId + "/cancel", { method: "POST" });
    log("cancel → " + JSON.stringify(await resp.json()));
  } catch (e) { log("cancel_error → " + e); }
};
refreshButtons();
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(HTML_PAGE)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("GATEWAY_PORT", "8000")))

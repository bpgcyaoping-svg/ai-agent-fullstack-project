"""Step 8 — CLI 客户端（httpx 直接消费 SSE）。

用法：
    python client.py create "提示词"       # 创建 Run 并订阅
    python client.py subscribe run_xxx     # 订阅已有 Run
    python client.py resume                # 按本地游标断线续传
    python client.py cancel run_xxx        # 显式取消
    python client.py status [run_xxx]      # 查看详情 / 列表

断线恢复演示：每收到一个事件就把 (run_id, seq) 写入 .client_cursor.json，
resume 时带上 after_seq=seq，服务端只推增量（Replay 语义）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

from sse import SSEDecoder

BASE_URL = "http://127.0.0.1:8000"
CURSOR_FILE = Path(__file__).with_name(".client_cursor.json")


def status(message: str) -> None:
    print(message, file=sys.stderr)


def load_cursor() -> dict[str, Any]:
    if not CURSOR_FILE.exists():
        return {}
    try:
        return json.loads(CURSOR_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_cursor(run_id: str, seq: int) -> None:
    CURSOR_FILE.write_text(
        json.dumps({"run_id": run_id, "seq": seq}, ensure_ascii=False),
        encoding="utf-8",
    )


def handle_event(event: dict[str, Any]) -> bool:
    """处理一条事件；返回 True 表示已到终态。"""
    etype = event["type"]
    if etype == "text.delta":
        sys.stdout.write(event["data"]["delta"])
        sys.stdout.flush()
        return False
    if etype == "run.completed":
        print()
        metrics = event["data"].get("metrics") or {}
        status(
            f"[status] completed ttft={metrics.get('ttft_seconds')}s "
            f"total={metrics.get('total_seconds')}s "
            f"usage={json.dumps(event['data'].get('usage') or {}, ensure_ascii=False)}"
        )
        return True
    if etype == "run.failed":
        print()
        data = event["data"]
        status(
            f"[status] failed code={data.get('code')} "
            f"retryable={data.get('retryable')} message={data.get('message')}"
        )
        return True
    if etype == "run.cancelled":
        print()
        status(f"[status] cancelled reason={event['data'].get('reason')}")
        return True
    status(f"[event] #{event['seq']} {etype}")
    return False


def consume_events(run_id: str, after_seq: int) -> int:
    """订阅 SSE 流直到终态或断线；返回最后收到的 seq。"""
    decoder = SSEDecoder()
    last_seq = after_seq
    saw_terminal = False

    try:
        with httpx.Client(timeout=None) as client:
            with client.stream(
                "GET",
                f"{BASE_URL}/v1/runs/{run_id}/events",
                params={"after_seq": after_seq},
            ) as response:
                response.raise_for_status()
                status(f"[status] subscribed run_id={run_id} after_seq={after_seq}")

                for line in response.iter_lines():
                    frame = decoder.feed_line(line)
                    if frame is None or not frame.data:
                        continue
                    event = json.loads(frame.data)
                    last_seq = max(last_seq, int(frame.id or event["seq"]))
                    save_cursor(run_id, last_seq)  # 游标实时落盘，断线可续传
                    if handle_event(event):
                        saw_terminal = True
                        break
    except httpx.HTTPStatusError as exc:
        status(f"[status] failed code=HTTP_{exc.response.status_code}")
        return last_seq
    except KeyboardInterrupt:
        status("[status] interrupted（Ctrl+C 只断开订阅，Run 仍在服务端运行）")
        return last_seq

    if saw_terminal:
        status(f"[status] done last_seq={last_seq}")
    else:
        status(f"[status] disconnected last_seq={last_seq} → 可执行 python client.py resume 续传")
    return last_seq


def cmd_create(args: argparse.Namespace) -> None:
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(f"{BASE_URL}/v1/runs", json={"prompt": args.prompt})
        resp.raise_for_status()
        payload = resp.json()
    status(f"[status] created run_id={payload['run_id']}")
    save_cursor(payload["run_id"], -1)
    consume_events(payload["run_id"], -1)


def cmd_subscribe(args: argparse.Namespace) -> None:
    consume_events(args.run_id, args.after_seq)


def cmd_resume(args: argparse.Namespace) -> None:
    cursor = load_cursor()
    run_id = args.run_id or cursor.get("run_id")
    if not run_id:
        raise SystemExit("没有可恢复的 Run，请先 create 或 subscribe")
    after_seq = int(cursor.get("seq", -1))
    status(f"[status] resume run_id={run_id} from cursor seq={after_seq}")
    consume_events(run_id, after_seq)


def cmd_cancel(args: argparse.Namespace) -> None:
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(f"{BASE_URL}/v1/runs/{args.run_id}/cancel")
        resp.raise_for_status()
        print(json.dumps(resp.json(), ensure_ascii=False))


def cmd_status(args: argparse.Namespace) -> None:
    with httpx.Client(timeout=10.0) as client:
        url = (
            f"{BASE_URL}/v1/runs/{args.run_id}"
            if args.run_id
            else f"{BASE_URL}/v1/runs"
        )
        resp = client.get(url)
        resp.raise_for_status()
        print(json.dumps(resp.json(), ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Streaming Agent Gateway CLI 客户端")
    parser.add_argument("--base-url", default=BASE_URL, help="网关地址")

    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="创建 Run 并订阅")
    p_create.add_argument("prompt")

    p_sub = sub.add_parser("subscribe", help="订阅已有 Run")
    p_sub.add_argument("run_id")
    p_sub.add_argument("--after-seq", type=int, default=-1)

    p_resume = sub.add_parser("resume", help="按本地游标断线续传")
    p_resume.add_argument("run_id", nargs="?")

    p_cancel = sub.add_parser("cancel", help="显式取消 Run")
    p_cancel.add_argument("run_id")

    p_status = sub.add_parser("status", help="查看 Run 详情 / 列表")
    p_status.add_argument("run_id", nargs="?")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    global BASE_URL
    BASE_URL = args.base_url.rstrip("/")

    commands = {
        "create": cmd_create,
        "subscribe": cmd_subscribe,
        "resume": cmd_resume,
        "cancel": cmd_cancel,
        "status": cmd_status,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()

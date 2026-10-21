"""Step 2 — SSE 编解码。

服务端把 RunEvent 编码成 SSE 文本帧，客户端把文本行流解码回帧：

    id: 3
    event: text.delta
    data: {"run_id": "...", "seq": 3, ...}

- id 行 = RunEvent.seq：浏览器 EventSource 断线自动重连时会带上
  Last-Event-ID 头，服务端据此只推增量 → 可恢复（Replay）。
- ": ..." 开头是注释帧，用作心跳，双方解析时都忽略。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from events import RunEvent


def encode_sse(event: RunEvent) -> str:
    """RunEvent → SSE 帧。id 字段承载 seq，供断线续传。"""
    payload = json.dumps(event.to_dict(), ensure_ascii=False)
    return f"id: {event.seq}\nevent: {event.type}\ndata: {payload}\n\n"


def encode_heartbeat() -> str:
    """注释帧心跳：防止代理把空闲长连接掐断。"""
    return ": keep-alive\n\n"


@dataclass
class SSEFrame:
    id: str | None
    event: str | None
    data: str


def parse_sse_frame(lines: list[str]) -> SSEFrame:
    """一帧的文本行 → SSEFrame。注释行（心跳）直接忽略。"""
    frame_id: str | None = None
    event_name: str | None = None
    data_lines: list[str] = []

    for line in lines:
        if line.startswith(":"):
            continue
        field_name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field_name == "id":
            frame_id = value
        elif field_name == "event":
            event_name = value
        elif field_name == "data":
            data_lines.append(value)

    return SSEFrame(frame_id, event_name, "\n".join(data_lines))


class SSEDecoder:
    """流式解码器：不断喂入文本行，遇到空行时切出一帧。"""

    def __init__(self) -> None:
        self._buffer: list[str] = []

    def feed_line(self, line: str) -> SSEFrame | None:
        if line == "":
            if not self._buffer:
                return None
            frame = parse_sse_frame(self._buffer)
            self._buffer = []
            return frame
        if line.startswith(":"):
            return None  # 注释/心跳行直接忽略，不进入帧缓冲
        self._buffer.append(line)
        return None

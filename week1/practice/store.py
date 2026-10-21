"""Step 5 / Step 10 — RunStore：Run 元数据、事件流、CheckPoint 的持久化。

- InMemoryRunStore: 单元测试用
- SqliteRunStore:   默认实现（标准库 sqlite3，零额外依赖）
  * events 表按 (run_id, seq) 主键落库 → 重启后仍可按游标 Replay（可恢复）
  * runs + events 表合起来就是完整 Trace（可审计）
  * checkpoints 表保存最近一次状态快照（CheckPoint）

sqlite 写入是微秒级，教学实现直接在 async 函数里同步调用；
check_same_thread=False 允许 FastAPI/TestClient 的不同线程访问同一连接。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from events import RunEvent, now_ms


@dataclass
class RunRecord:
    """runs 表对应的 Run 元数据。"""

    run_id: str
    prompt: str
    status: str  # created/running/completed/failed/cancelled
    created_at: int = field(default_factory=now_ms)
    updated_at: int = field(default_factory=now_ms)
    metrics: dict[str, Any] | None = None  # TTFT 等统计快照


@dataclass
class CheckPoint:
    """Run 的状态快照：崩溃/重启后据此还原进度（可恢复）。"""

    run_id: str
    status: str
    next_seq: int
    partial_text: str
    usage: dict[str, Any]
    metrics: dict[str, Any] | None
    updated_at: int = field(default_factory=now_ms)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunStore(Protocol):
    """持久化协议：内存实现与 SQLite 实现都遵守同一接口。"""

    def create_run(self, record: RunRecord) -> None: ...

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None: ...

    def get_run(self, run_id: str) -> RunRecord | None: ...

    def list_runs(self) -> list[RunRecord]: ...

    def append_event(self, event: RunEvent) -> None: ...

    def get_events(self, run_id: str, after_seq: int = -1) -> list[RunEvent]: ...

    def save_checkpoint(self, checkpoint: CheckPoint) -> None: ...

    def get_checkpoint(self, run_id: str) -> CheckPoint | None: ...


class InMemoryRunStore:
    """纯内存实现，用于单元测试。"""

    def __init__(self) -> None:
        self._runs: dict[str, RunRecord] = {}
        self._events: dict[str, list[RunEvent]] = {}
        self._checkpoints: dict[str, CheckPoint] = {}

    def create_run(self, record: RunRecord) -> None:
        self._runs[record.run_id] = record

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        record = self._runs.get(run_id)
        if record is None:
            return
        if status is not None:
            record.status = status
        if metrics is not None:
            record.metrics = metrics
        record.updated_at = now_ms()

    def get_run(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    def list_runs(self) -> list[RunRecord]:
        return list(self._runs.values())

    def append_event(self, event: RunEvent) -> None:
        self._events.setdefault(event.run_id, []).append(event)

    def get_events(self, run_id: str, after_seq: int = -1) -> list[RunEvent]:
        return [e for e in self._events.get(run_id, []) if e.seq > after_seq]

    def save_checkpoint(self, checkpoint: CheckPoint) -> None:
        self._checkpoints[checkpoint.run_id] = checkpoint

    def get_checkpoint(self, run_id: str) -> CheckPoint | None:
        return self._checkpoints.get(run_id)


class SqliteRunStore:
    """SQLite 持久化实现（Step 10：接入数据库与 CheckPoint）。"""

    def __init__(self, db_path: str | Path = "gateway_runs.db") -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def close(self) -> None:
        self._conn.close()

    def _create_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id     TEXT PRIMARY KEY,
                prompt     TEXT NOT NULL,
                status     TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                metrics    TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                run_id TEXT NOT NULL,
                seq    INTEGER NOT NULL,
                type   TEXT NOT NULL,
                data   TEXT NOT NULL,
                ts     INTEGER NOT NULL,
                PRIMARY KEY (run_id, seq)
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                run_id       TEXT PRIMARY KEY,
                status       TEXT NOT NULL,
                next_seq     INTEGER NOT NULL,
                partial_text TEXT NOT NULL,
                usage        TEXT NOT NULL,
                metrics      TEXT,
                updated_at   INTEGER NOT NULL
            );
            """
        )

    # ---- runs ----

    def create_run(self, record: RunRecord) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?, ?)",
            (
                record.run_id,
                record.prompt,
                record.status,
                record.created_at,
                record.updated_at,
                json.dumps(record.metrics, ensure_ascii=False)
                if record.metrics is not None
                else None,
            ),
        )
        self._conn.commit()

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        record = self.get_run(run_id)
        if record is None:
            return
        if status is not None:
            record.status = status
        if metrics is not None:
            record.metrics = metrics
        record.updated_at = now_ms()
        self.create_run(record)

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return RunRecord(
            run_id=row["run_id"],
            prompt=row["prompt"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metrics=json.loads(row["metrics"]) if row["metrics"] else None,
        )

    def list_runs(self) -> list[RunRecord]:
        rows = self._conn.execute(
            "SELECT run_id FROM runs ORDER BY created_at DESC"
        ).fetchall()
        runs = []
        for row in rows:
            record = self.get_run(row["run_id"])
            if record is not None:
                runs.append(record)
        return runs

    # ---- events ----

    def append_event(self, event: RunEvent) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?)",
            (
                event.run_id,
                event.seq,
                event.type,
                json.dumps(event.data, ensure_ascii=False),
                event.ts,
            ),
        )
        self._conn.commit()

    def get_events(self, run_id: str, after_seq: int = -1) -> list[RunEvent]:
        rows = self._conn.execute(
            "SELECT * FROM events WHERE run_id = ? AND seq > ? ORDER BY seq",
            (run_id, after_seq),
        ).fetchall()
        return [
            RunEvent(
                run_id=row["run_id"],
                seq=row["seq"],
                type=row["type"],
                data=json.loads(row["data"]),
                ts=row["ts"],
            )
            for row in rows
        ]

    # ---- checkpoints ----

    def save_checkpoint(self, checkpoint: CheckPoint) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                checkpoint.run_id,
                checkpoint.status,
                checkpoint.next_seq,
                checkpoint.partial_text,
                json.dumps(checkpoint.usage, ensure_ascii=False),
                json.dumps(checkpoint.metrics, ensure_ascii=False)
                if checkpoint.metrics is not None
                else None,
                checkpoint.updated_at,
            ),
        )
        self._conn.commit()

    def get_checkpoint(self, run_id: str) -> CheckPoint | None:
        row = self._conn.execute(
            "SELECT * FROM checkpoints WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        return CheckPoint(
            run_id=row["run_id"],
            status=row["status"],
            next_seq=row["next_seq"],
            partial_text=row["partial_text"],
            usage=json.loads(row["usage"]),
            metrics=json.loads(row["metrics"]) if row["metrics"] else None,
            updated_at=row["updated_at"],
        )

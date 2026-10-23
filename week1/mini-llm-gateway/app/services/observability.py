from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass
class UsageRecord:
    request_id: str
    model: str
    protocol: str
    upstream_model: str | None = None
    stream: bool = False
    prompt_id: str | None = None
    prompt_version: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    latency_ms: float = 0
    ttft_ms: float | None = None
    retries: int = 0
    status: str = "success"
    status_code: int = 200
    error_code: str | None = None
    error_message: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["total_tokens"] = self.total_tokens
        return data


class UsageStore:
    """SQLite ledger for tokens, latency, TTFT and retry/error outcomes."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    request_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    model TEXT NOT NULL,
                    protocol TEXT NOT NULL,
                    upstream_model TEXT,
                    stream INTEGER NOT NULL,
                    prompt_id TEXT,
                    prompt_version INTEGER,
                    prompt_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    cached_tokens INTEGER NOT NULL,
                    reasoning_tokens INTEGER NOT NULL,
                    latency_ms REAL NOT NULL,
                    ttft_ms REAL,
                    retries INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    status_code INTEGER NOT NULL,
                    error_code TEXT,
                    error_message TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_events(created_at)"
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_events(model)")

    def record(self, record: UsageRecord) -> None:
        values = record.to_dict()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO usage_events(
                    request_id, created_at, model, protocol, upstream_model, stream,
                    prompt_id, prompt_version, prompt_tokens, completion_tokens,
                    cached_tokens, reasoning_tokens, latency_ms, ttft_ms, retries,
                    status, status_code, error_code, error_message
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    values["request_id"],
                    datetime.now(timezone.utc).isoformat(),
                    values["model"],
                    values["protocol"],
                    values["upstream_model"],
                    int(values["stream"]),
                    values["prompt_id"],
                    values["prompt_version"],
                    values["prompt_tokens"],
                    values["completion_tokens"],
                    values["cached_tokens"],
                    values["reasoning_tokens"],
                    values["latency_ms"],
                    values["ttft_ms"],
                    values["retries"],
                    values["status"],
                    values["status_code"],
                    values["error_code"],
                    values["error_message"],
                ),
            )

    def recent(self, limit: int = 50, model: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM usage_events"
        params: list[Any] = []
        if model:
            query += " WHERE model = ?"
            params.append(model)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(min(limit, 1000))
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM usage_events WHERE request_id = ?", (request_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def metrics(self) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT model, protocol,
                       COUNT(*) AS requests,
                       SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes,
                       SUM(CASE WHEN status != 'success' THEN 1 ELSE 0 END) AS errors,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total_tokens,
                       COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                       COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens,
                       AVG(latency_ms) AS avg_latency_ms,
                       AVG(ttft_ms) AS avg_ttft_ms,
                       MAX(latency_ms) AS max_latency_ms
                FROM usage_events
                GROUP BY model, protocol
                ORDER BY model
                """
            ).fetchall()
            totals = connection.execute(
                """
                SELECT COUNT(*) AS requests,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS total_tokens,
                       COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                       COALESCE(SUM(reasoning_tokens), 0) AS reasoning_tokens
                FROM usage_events
                """
            ).fetchone()

        models = []
        for row in rows:
            item = dict(row)
            item["avg_latency_ms"] = round(item["avg_latency_ms"] or 0, 2)
            item["avg_ttft_ms"] = (
                round(item["avg_ttft_ms"], 2) if item["avg_ttft_ms"] is not None else None
            )
            item["max_latency_ms"] = round(item["max_latency_ms"] or 0, 2)
            models.append(item)
        return {"models": models, "totals": dict(totals)}

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["stream"] = bool(data["stream"])
        data["total_tokens"] = data["prompt_tokens"] + data["completion_tokens"]
        return data

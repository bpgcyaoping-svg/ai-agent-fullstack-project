from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

from app.core.errors import GatewayError
from app.schemas import PromptCreate, PromptRecord


class PromptRepository:
    """Versioned prompt storage with strict Jinja2 variable rendering."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self.env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS prompts (
                    id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (id, version)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_prompts_active ON prompts(id, is_active)"
            )

    def create_version(self, prompt: PromptCreate) -> PromptRecord:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM prompts WHERE id = ?", (prompt.id,)
            )
            version = int(cursor.fetchone()[0])
            if prompt.activate:
                connection.execute("UPDATE prompts SET is_active = 0 WHERE id = ?", (prompt.id,))
            connection.execute(
                """
                INSERT INTO prompts(id, version, name, description, role, content, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    prompt.id,
                    version,
                    prompt.name,
                    prompt.description,
                    prompt.role,
                    prompt.content,
                    int(prompt.activate),
                    now,
                ),
            )
        return PromptRecord(
            id=prompt.id,
            version=version,
            name=prompt.name,
            description=prompt.description,
            role=prompt.role,
            content=prompt.content,
            is_active=prompt.activate,
            created_at=now,
        )

    def get(self, prompt_id: str, version: int | None = None) -> PromptRecord:
        query = (
            "SELECT id, version, name, description, role, content, is_active, created_at "
            "FROM prompts WHERE id = ?"
        )
        params: tuple[Any, ...] = (prompt_id,)
        if version is None:
            query += " AND is_active = 1 ORDER BY version DESC LIMIT 1"
        else:
            query += " AND version = ?"
            params = (prompt_id, version)
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        if row is None:
            raise GatewayError(
                f"Prompt {prompt_id!r} version {version if version is not None else 'active'} was not found",
                status_code=404,
                error_type="invalid_request_error",
                code="prompt_not_found",
                param="prompt.id",
            )
        return PromptRecord(**dict(row))

    def list(self) -> list[PromptRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT id, version, name, description, role, content, is_active, created_at "
                "FROM prompts ORDER BY id, version DESC"
            ).fetchall()
        return [PromptRecord(**dict(row)) for row in rows]

    def render(
        self, prompt_id: str, variables: dict[str, Any], version: int | None = None
    ) -> tuple[PromptRecord, str]:
        prompt = self.get(prompt_id, version)
        try:
            rendered = self.env.from_string(prompt.content).render(**variables)
        except Exception as exc:  # jinja2 raises several exception types
            raise GatewayError(
                f"Prompt rendering failed: {exc}",
                status_code=422,
                error_type="invalid_request_error",
                code="prompt_render_error",
                param="prompt.variables",
            ) from exc
        return prompt, rendered

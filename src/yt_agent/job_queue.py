"""Persistent, synchronous job queue with bounded retry metadata."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Literal

from yt_agent.errors import InvalidInputError
from yt_agent.security import ensure_private_file

JobOperation = Literal["download", "index", "sync"]
JOB_OPERATIONS = {"download", "index", "sync"}
MAX_RETRIES = 10


class _ClosingConnection(sqlite3.Connection):
    """Commit or roll back a context block, then release the SQLite handle."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL CHECK(operation IN ('download', 'index', 'sync')),
    target TEXT NOT NULL,
    options_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL CHECK(
        status IN ('pending', 'running', 'completed', 'failed', 'cancelled')
    ),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    available_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_runnable ON jobs(status, available_at, job_id);
"""


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class QueueJob:
    job_id: int
    operation: JobOperation
    target: str
    options: dict[str, Any]
    status: str
    attempts: int
    max_attempts: int
    available_at: str
    last_error: str | None
    created_at: str
    updated_at: str

    @property
    def retries_remaining(self) -> int:
        return max(self.max_attempts - self.attempts, 0)


def _row_to_job(row: sqlite3.Row) -> QueueJob:
    options = json.loads(str(row["options_json"]))
    return QueueJob(
        job_id=int(row["job_id"]),
        operation=str(row["operation"]),  # type: ignore[arg-type]
        target=str(row["target"]),
        options=options if isinstance(options, dict) else {},
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
        available_at=str(row["available_at"]),
        last_error=str(row["last_error"]) if row["last_error"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class JobQueue:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        ensure_private_file(self.path)
        conn = sqlite3.connect(self.path, factory=_ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def add(
        self,
        operation: str,
        target: str,
        *,
        options: dict[str, Any] | None = None,
        max_retries: int = 2,
    ) -> QueueJob:
        if operation not in JOB_OPERATIONS:
            raise InvalidInputError("Queue operation must be download, index, or sync.")
        if not target.strip():
            raise InvalidInputError("Queue target cannot be empty.")
        if not 0 <= max_retries <= MAX_RETRIES:
            raise InvalidInputError(f"Max retries must be between 0 and {MAX_RETRIES}.")
        timestamp = _now().isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO jobs (
                    operation, target, options_json, status, attempts, max_attempts,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'pending', 0, ?, ?, ?, ?)
                """,
                (
                    operation,
                    target.strip(),
                    json.dumps(options or {}, sort_keys=True),
                    max_retries + 1,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            job_id = int(cursor.lastrowid or 0)
        return self._required(job_id)

    def get(self, job_id: int) -> QueueJob | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row is not None else None

    def list(self, *, status: str | None = None) -> list[QueueJob]:
        with self.connect() as conn:
            if status is None:
                rows = conn.execute("SELECT * FROM jobs ORDER BY job_id").fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY job_id", (status,)
                ).fetchall()
        return [_row_to_job(row) for row in rows]

    def peek_next(self) -> QueueJob | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'pending' AND available_at <= ?
                ORDER BY available_at, job_id LIMIT 1
                """,
                (_now().isoformat(),),
            ).fetchone()
        return _row_to_job(row) if row is not None else None

    def claim_next(self) -> QueueJob | None:
        timestamp = _now().isoformat()
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT job_id FROM jobs
                WHERE status = 'pending' AND available_at <= ?
                ORDER BY available_at, job_id LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                return None
            job_id = int(row["job_id"])
            conn.execute(
                """
                UPDATE jobs SET status = 'running', attempts = attempts + 1, updated_at = ?
                WHERE job_id = ?
                """,
                (timestamp, job_id),
            )
        return self.get(job_id)

    def recover_running(self) -> int:
        """Requeue jobs abandoned by a previous synchronous worker process."""
        timestamp = _now().isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE jobs SET status = 'pending', available_at = ?, updated_at = ?,
                    last_error = 'Recovered after an interrupted queue worker.'
                WHERE status = 'running'
                """,
                (timestamp, timestamp),
            )
        return cursor.rowcount

    def complete(self, job_id: int) -> QueueJob:
        return self._transition(job_id, status="completed", last_error=None)

    def fail(self, job_id: int, error: str) -> QueueJob:
        job = self._required(job_id)
        retry = job.attempts < job.max_attempts
        delay_seconds = min(2 ** max(job.attempts - 1, 0), 300)
        available_at = (_now() + timedelta(seconds=delay_seconds)).isoformat()
        return self._transition(
            job_id,
            status="pending" if retry else "failed",
            last_error=error[:1000],
            available_at=available_at,
        )

    def cancel(self, job_id: int) -> QueueJob:
        job = self._required(job_id)
        if job.status not in {"pending", "failed"}:
            raise InvalidInputError(f"Job {job_id} cannot be cancelled from {job.status} state.")
        return self._transition(job_id, status="cancelled", last_error=job.last_error)

    def retry(self, job_id: int) -> QueueJob:
        job = self._required(job_id)
        if job.status not in {"failed", "cancelled"}:
            raise InvalidInputError(f"Job {job_id} cannot be retried from {job.status} state.")
        timestamp = _now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs SET status = 'pending', attempts = 0, available_at = ?,
                    last_error = NULL, updated_at = ? WHERE job_id = ?
                """,
                (timestamp, timestamp, job_id),
            )
        return self._required(job_id)

    def _required(self, job_id: int) -> QueueJob:
        job = self.get(job_id)
        if job is None:
            raise InvalidInputError(f"Queue job {job_id} was not found.")
        return job

    def _transition(
        self,
        job_id: int,
        *,
        status: str,
        last_error: str | None,
        available_at: str | None = None,
    ) -> QueueJob:
        timestamp = _now().isoformat()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs SET status = ?, last_error = ?,
                    available_at = COALESCE(?, available_at),
                    updated_at = ? WHERE job_id = ?
                """,
                (status, last_error, available_at, timestamp, job_id),
            )
        return self._required(job_id)


def job_payload(job: QueueJob) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "operation": job.operation,
        "target": job.target,
        "options": job.options,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "retries_remaining": job.retries_remaining,
        "available_at": job.available_at,
        "last_error": job.last_error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }

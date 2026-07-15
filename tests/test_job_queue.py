from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from yt_agent.errors import InvalidInputError
from yt_agent.job_queue import JobQueue


def test_queue_persists_and_claims_only_once(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "jobs.sqlite")
    queue.ensure_schema()
    added = queue.add(
        "download",
        "abc123def45",
        options={"audio": True},
        max_retries=2,
    )

    reopened = JobQueue(queue.path)
    job = reopened.get(added.job_id)
    assert job is not None
    assert job.options == {"audio": True}
    assert job.max_attempts == 3

    claimed = reopened.claim_next()
    assert claimed is not None
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert reopened.claim_next() is None

    completed = reopened.complete(claimed.job_id)
    assert completed.status == "completed"


def test_queue_recovers_job_abandoned_by_interrupted_worker(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "jobs.sqlite")
    queue.ensure_schema()
    queue.add("sync", "abc123def45")
    claimed = queue.claim_next()
    assert claimed is not None and claimed.status == "running"

    assert queue.recover_running() == 1
    recovered = queue.peek_next()
    assert recovered is not None
    assert recovered.status == "pending"
    assert recovered.attempts == 1
    assert recovered.last_error == "Recovered after an interrupted queue worker."


def test_queue_failure_uses_bounded_backoff_and_manual_retry(tmp_path: Path, monkeypatch) -> None:
    import yt_agent.job_queue as job_queue

    current = datetime(2026, 7, 10, tzinfo=UTC)
    monkeypatch.setattr(job_queue, "_now", lambda: current)
    queue = JobQueue(tmp_path / "jobs.sqlite")
    queue.ensure_schema()
    added = queue.add("index", "abc123def45", max_retries=1)
    claimed = queue.claim_next()
    assert claimed is not None

    pending = queue.fail(claimed.job_id, "temporary failure")
    assert pending.status == "pending"
    assert pending.available_at == (current + timedelta(seconds=1)).isoformat()
    assert queue.peek_next() is None

    current += timedelta(seconds=1)
    second = queue.claim_next()
    assert second is not None
    failed = queue.fail(second.job_id, "permanent failure")
    assert failed.status == "failed"
    assert failed.retries_remaining == 0

    retried = queue.retry(added.job_id)
    assert retried.status == "pending"
    assert retried.attempts == 0
    assert retried.last_error is None


def test_queue_cancel_and_input_validation(tmp_path: Path) -> None:
    queue = JobQueue(tmp_path / "jobs.sqlite")
    queue.ensure_schema()
    job = queue.add("sync", "https://www.youtube.com/playlist?list=PL123")
    assert queue.cancel(job.job_id).status == "cancelled"

    with pytest.raises(InvalidInputError, match="operation"):
        queue.add("shell", "echo unsafe")
    with pytest.raises(InvalidInputError, match="between 0 and 10"):
        queue.add("download", "abc123def45", max_retries=11)
    retried = queue.retry(job.job_id)
    claimed = queue.claim_next()
    assert claimed is not None and claimed.job_id == retried.job_id
    queue.complete(claimed.job_id)
    with pytest.raises(InvalidInputError, match="cannot be cancelled"):
        queue.cancel(claimed.job_id)

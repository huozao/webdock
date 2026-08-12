from __future__ import annotations

import asyncio
import copy
import hashlib
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


JobRunner = Callable[[], Awaitable[dict[str, Any]]]
ProgressProvider = Callable[[], dict[str, Any]]


class JobConflictError(Exception):
    pass


class JobCapacityError(Exception):
    pass


@dataclass
class _Job:
    job_id: str
    request_id: str
    fingerprint: str
    status: str = "queued"
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    task: asyncio.Task[None] | None = None
    progress_provider: ProgressProvider | None = None


class ChatJobStore:
    """In-process registry for browser jobs.

    The browser task itself is node-local, so callers must keep polling the same
    WebDock route that accepted the submission.
    """

    def __init__(
        self,
        *,
        retention_seconds: float = 86400,
        max_completed_jobs: int = 1000,
        max_active_jobs: int = 100,
        execution_timeout_seconds: float = 1200,
    ) -> None:
        self._jobs: dict[str, _Job] = {}
        self._request_jobs: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._retention_ms = max(60_000, int(retention_seconds * 1000))
        self._max_completed_jobs = max(1, int(max_completed_jobs))
        self._max_active_jobs = max(1, int(max_active_jobs))
        self._execution_timeout_seconds = max(0.001, float(execution_timeout_seconds))

    async def submit(
        self,
        *,
        request_id: str,
        fingerprint: str,
        runner: JobRunner,
        progress_provider: ProgressProvider | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._prune_locked()
            existing_id = self._request_jobs.get(request_id)
            if existing_id:
                existing = self._jobs[existing_id]
                if existing.fingerprint != fingerprint:
                    raise JobConflictError(
                        f"request_id {request_id} was reused with a different payload"
                    )
                return self._snapshot(existing)

            active_jobs = sum(
                job.task is not None and not job.task.done() for job in self._jobs.values()
            )
            if active_jobs >= self._max_active_jobs:
                raise JobCapacityError(
                    f"WebDock async job queue is full ({active_jobs}/{self._max_active_jobs})"
                )

            job_id = "job-" + hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:24]
            job = _Job(
                job_id=job_id,
                request_id=request_id,
                fingerprint=fingerprint,
                progress_provider=progress_provider,
            )
            self._jobs[job_id] = job
            self._request_jobs[request_id] = job_id
            job.task = asyncio.create_task(self._run(job, runner), name=f"webdock-{job_id}")
            return self._snapshot(job)

    async def get(self, job_id: str) -> dict[str, Any] | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            return self._snapshot(job) if job else None

    async def cancel(self, job_id: str) -> dict[str, Any] | None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status not in {"succeeded", "failed", "cancelled"}:
                job.status = "cancelled"
                job.finished_at_ms = int(time.time() * 1000)
                job.error = {
                    "ok": False,
                    "error_code": "REQUEST_CANCELLED",
                    "message": "WebDock job was cancelled.",
                }
            if job.task is not None and not job.task.done():
                job.task.cancel()
            snapshot = self._snapshot(job)
            self._prune_locked()
            return snapshot

    async def close(self) -> None:
        async with self._lock:
            tasks = [job.task for job in self._jobs.values() if job.task and not job.task.done()]
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, job: _Job, runner: JobRunner) -> None:
        async with self._lock:
            if job.status == "cancelled":
                return
            job.status = "running"
            job.started_at_ms = int(time.time() * 1000)
        try:
            result = await asyncio.wait_for(runner(), timeout=self._execution_timeout_seconds)
        except asyncio.TimeoutError:
            async with self._lock:
                if job.status != "cancelled":
                    job.status = "failed"
                    job.finished_at_ms = int(time.time() * 1000)
                    job.error = {
                        "ok": False,
                        "error_code": "RESPONSE_TIMEOUT",
                        "message": (
                            "WebDock job exceeded the full lifecycle cap of "
                            f"{self._execution_timeout_seconds:.0f}s."
                        ),
                    }
                self._prune_locked()
        except asyncio.CancelledError:
            async with self._lock:
                if job.status != "cancelled":
                    job.status = "cancelled"
                    job.finished_at_ms = int(time.time() * 1000)
                    job.error = {
                        "ok": False,
                        "error_code": "REQUEST_CANCELLED",
                        "message": "WebDock job was cancelled.",
                    }
                self._prune_locked()
        except Exception as exc:  # route runner attaches structured detail when available
            detail = getattr(exc, "detail", None)
            if not isinstance(detail, dict):
                detail = {
                    "ok": False,
                    "error_code": "UNKNOWN_ERROR",
                    "message": str(exc),
                }
            async with self._lock:
                if job.status != "cancelled":
                    job.status = "failed"
                    job.finished_at_ms = int(time.time() * 1000)
                    job.error = detail
                self._prune_locked()
        else:
            async with self._lock:
                if job.status != "cancelled":
                    job.status = "succeeded"
                    job.finished_at_ms = int(time.time() * 1000)
                    job.result = result
                self._prune_locked()

    @staticmethod
    def _snapshot(job: _Job) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": job.job_id,
            "request_id": job.request_id,
            "status": job.status,
            "created_at_ms": job.created_at_ms,
        }
        if job.started_at_ms is not None:
            payload["started_at_ms"] = job.started_at_ms
        if job.finished_at_ms is not None:
            payload["finished_at_ms"] = job.finished_at_ms
        if job.result is not None:
            payload["result"] = copy.deepcopy(job.result)
        if job.error is not None:
            payload["error"] = copy.deepcopy(job.error)
        if job.progress_provider is not None:
            try:
                progress = job.progress_provider()
            except Exception:
                progress = None
            if isinstance(progress, dict):
                payload["progress"] = copy.deepcopy(progress)
        elapsed_start = job.started_at_ms or job.created_at_ms
        elapsed_end = job.finished_at_ms or int(time.time() * 1000)
        payload["elapsed_seconds"] = round(max(0, elapsed_end - elapsed_start) / 1000, 1)
        return payload

    def _prune_locked(self) -> None:
        now_ms = int(time.time() * 1000)
        terminal = sorted(
            (
                job
                for job in self._jobs.values()
                if job.status in {"succeeded", "failed", "cancelled"}
                and job.finished_at_ms is not None
            ),
            key=lambda item: item.finished_at_ms or 0,
            reverse=True,
        )
        for index, job in enumerate(terminal):
            expired = now_ms - (job.finished_at_ms or now_ms) > self._retention_ms
            if expired or index >= self._max_completed_jobs:
                self._jobs.pop(job.job_id, None)
                if self._request_jobs.get(job.request_id) == job.job_id:
                    self._request_jobs.pop(job.request_id, None)

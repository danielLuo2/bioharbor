"""Persistent job store and GPU-aware runner.

Every tool call — inline or background — is recorded as a job in SQLite, which gives
provenance for free and lets the server survive restarts.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import gpu as gpu_mod
from .registry import ToolSpec

QUEUED = "queued"
WAITING_GPU = "waiting_gpu"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL = {SUCCEEDED, FAILED, CANCELLED}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    tool TEXT NOT NULL,
    params TEXT NOT NULL,
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    gpu INTEGER,
    created REAL NOT NULL,
    started REAL,
    finished REAL
);
CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created);
"""


@dataclass
class Job:
    id: str
    tool: str
    params: dict[str, Any]
    status: str
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    gpu: int | None
    created: float
    started: float | None
    finished: float | None

    @property
    def done(self) -> bool:
        return self.status in TERMINAL

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"job_id": self.id, "tool": self.tool, "status": self.status}
        if self.gpu is not None:
            d["gpu"] = self.gpu
        if self.started and self.finished:
            d["duration_s"] = round(self.finished - self.started, 3)
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        return d


class JobStore:
    """Thread-safe SQLite job table."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()

    def create(self, tool: str, params: dict[str, Any]) -> Job:
        job = Job(
            uuid.uuid4().hex[:12], tool, params, QUEUED, None, None, None, time.time(), None, None
        )
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, tool, params, status, created) VALUES (?, ?, ?, ?, ?)",
                (job.id, tool, json.dumps(params), QUEUED, job.created),
            )
        return job

    def update(self, job_id: str, **fields: Any) -> None:
        for key in ("result", "error"):
            if key in fields and fields[key] is not None:
                fields[key] = json.dumps(fields[key])
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, limit: int = 20, status: str | None = None) -> list[Job]:
        sql, args = "SELECT * FROM jobs", []
        if status:
            sql, args = sql + " WHERE status = ?", [status]
        with self._lock:
            rows = self._conn.execute(
                sql + " ORDER BY created DESC LIMIT ?", (*args, limit)
            ).fetchall()
        return [self._row(r) for r in rows]

    def recover_interrupted(self) -> int:
        """Jobs left running/queued by a crashed or restarted server are marked failed."""
        err = json.dumps(
            {
                "error": "Interrupted",
                "message": "server restarted while the job was pending",
                "hint": "resubmit the job",
                "retryable": True,
            }
        )
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status = ?, error = ?, finished = ? WHERE status IN (?, ?, ?)",
                (FAILED, err, time.time(), QUEUED, WAITING_GPU, RUNNING),
            )
        return cur.rowcount

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @staticmethod
    def _row(row: tuple) -> Job:
        id_, tool, params, status, result, error, gpu, created, started, finished = row
        return Job(
            id_,
            tool,
            json.loads(params),
            status,
            json.loads(result) if result else None,
            json.loads(error) if error else None,
            gpu,
            created,
            started,
            finished,
        )


class GPUReservations:
    """Tracks memory we have promised to our own running jobs.

    NVML only shows memory once a process allocates it, so two jobs starting at the
    same moment would both see the GPU as free. Reservations close that gap.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reserved: dict[int, float] = {}

    def try_reserve(
        self,
        gpus: list[gpu_mod.GPUInfo],
        need_gb: float,
        held: Callable[[int], float] = lambda index: 0.0,
        **pick_kwargs: Any,
    ) -> tuple[int, float] | None:
        """Returns (gpu index, GB reserved). `held(index)` is memory our process already
        holds there and the job reuses, so only `need_gb - held` is newly reserved."""
        with self._lock:
            adjusted = [
                gpu_mod.GPUInfo(
                    g.index,
                    g.name,
                    g.mem_total_gb,
                    g.mem_free_gb - self._reserved.get(g.index, 0.0) + held(g.index),
                    g.util_pct,
                )
                for g in gpus
            ]
            chosen = gpu_mod.pick_gpu(adjusted, need_gb, **pick_kwargs)
            if chosen is None:
                return None
            amount = max(0.0, need_gb - held(chosen.index))
            self._reserved[chosen.index] = self._reserved.get(chosen.index, 0.0) + amount
            return chosen.index, amount

    def release(self, index: int, need_gb: float) -> None:
        with self._lock:
            self._reserved[index] = max(0.0, self._reserved.get(index, 0.0) - need_gb)


Executor = Callable[[ToolSpec, dict[str, Any], str, int | None], dict[str, Any]]
"""(spec, params, job_id, gpu_index) -> result dict. Raises to signal failure."""


class JobRunner:
    """Background workers that run slow tools, placing GPU tools on a GPU with room."""

    def __init__(
        self,
        store: JobStore,
        execute: Executor,
        *,
        workers: int = 2,
        gpu_probe: Callable[[], list[gpu_mod.GPUInfo]] = gpu_mod.probe,
        gpu_poll_s: float = 5.0,
        gpu_wait_timeout_s: float = 6 * 3600,
        gpu_headroom_gb: float = 1.0,
        gpu_max_util_pct: int = 90,
    ):
        self.store = store
        self._execute = execute
        self._probe = gpu_probe
        self._poll = gpu_poll_s
        self._gpu_wait_timeout = gpu_wait_timeout_s
        self._pick_kwargs = {"headroom_gb": gpu_headroom_gb, "max_util_pct": gpu_max_util_pct}
        self._reservations = GPUReservations()
        self._queue: queue.Queue[tuple[str, ToolSpec, Any] | None] = queue.Queue()
        self._events: dict[str, threading.Event] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()
        self._threads = [
            threading.Thread(target=self._worker, daemon=True, name=f"bh-worker-{i}")
            for i in range(workers)
        ]
        for t in self._threads:
            t.start()

    def submit(self, spec: ToolSpec, params: Any) -> str:
        """`params` is the validated pydantic model."""
        job = self.store.create(spec.name, params.model_dump(mode="json"))
        with self._lock:
            self._events[job.id] = threading.Event()
        self._queue.put((job.id, spec, params))
        return job.id

    def wait(self, job_id: str, timeout: float | None = None) -> Job | None:
        with self._lock:
            ev = self._events.get(job_id)
        if ev is not None:
            ev.wait(timeout)
        return self.store.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """Cancel a job that has not started executing yet."""
        job = self.store.get(job_id)
        if job is None or job.status not in (QUEUED, WAITING_GPU):
            return False
        with self._lock:
            self._cancelled.add(job_id)
        return True

    def shutdown(self) -> None:
        for _ in self._threads:
            self._queue.put(None)
        for t in self._threads:
            t.join(timeout=5)

    def _finish(self, job_id: str, **fields: Any) -> None:
        self.store.update(job_id, finished=time.time(), **fields)
        with self._lock:
            ev = self._events.pop(job_id, None)
            self._cancelled.discard(job_id)
        if ev:
            ev.set()

    def _is_cancelled(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancelled

    def _acquire_gpu(
        self, job_id: str, need_gb: float, held: Callable[[int], float]
    ) -> tuple[int, float] | None:
        deadline = time.monotonic() + self._gpu_wait_timeout
        waiting = False
        while not self._is_cancelled(job_id):
            gpus = self._probe()
            if not gpus:
                raise RuntimeError("no NVIDIA GPU visible (run `bioharbor doctor`)")
            got = self._reservations.try_reserve(gpus, need_gb, held, **self._pick_kwargs)
            if got is not None:
                return got
            if not waiting:
                self.store.update(job_id, status=WAITING_GPU)
                waiting = True
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"no GPU with {need_gb:.1f} GB free within {self._gpu_wait_timeout:.0f}s"
                )
            time.sleep(self._poll)
        return None

    def _worker(self) -> None:
        while (item := self._queue.get()) is not None:
            job_id, spec, params = item
            if self._is_cancelled(job_id):
                self._finish(job_id, status=CANCELLED)
                continue
            gpu_index, reserved_gb = None, 0.0
            try:
                if spec.resources.gpu:
                    got = self._acquire_gpu(
                        job_id, spec.resources.gpu_mem_for(params), spec.resources.gpu_mem_held_on
                    )
                    if got is None:
                        self._finish(job_id, status=CANCELLED)
                        continue
                    gpu_index, reserved_gb = got
                self.store.update(job_id, status=RUNNING, started=time.time(), gpu=gpu_index)
                result = self._execute(spec, params, job_id, gpu_index)
                self._finish(job_id, status=SUCCEEDED, result=result)
            except Exception as exc:  # recorded on the job, surfaced to the agent
                err = (
                    exc.to_dict()
                    if hasattr(exc, "to_dict")
                    else {
                        "error": type(exc).__name__,
                        "message": str(exc),
                        "hint": None,
                        "retryable": isinstance(exc, TimeoutError),
                    }
                )
                self._finish(job_id, status=FAILED, error=err)
            finally:
                if gpu_index is not None:
                    self._reservations.release(gpu_index, reserved_gb)

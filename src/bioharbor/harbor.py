"""The BioHarbor service: validates input, runs tools, records provenance.

Used by both the MCP server and the CLI, so `bioharbor run` behaves exactly like an
agent's tool call.
"""

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import __version__
from .errors import BioHarborError, InputValidationError
from .jobs import FAILED, RUNNING, SUCCEEDED, Job, JobRunner, JobStore
from .registry import RunContext, ToolSpec, get_tool, load_tools


def default_home() -> Path:
    return Path(os.environ.get("BIOHARBOR_HOME", Path.home() / ".bioharbor")).expanduser()


class Harbor:
    def __init__(
        self,
        home: Path | None = None,
        *,
        workers: int = 2,
        recover: bool = False,
        **runner_kwargs: Any,
    ):
        # NVML numbers GPUs by PCI bus; make CUDA (torch) use the same order so the GPU we
        # pick is the GPU the model actually lands on.
        os.environ.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
        self.home = (home or default_home()).resolve()
        self.jobs_dir = self.home / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.home / "bioharbor.sqlite")
        # Only the long-running server owns the queue; CLI helpers must not touch its jobs.
        self.recovered = self.store.recover_interrupted() if recover else 0
        self.runner = JobRunner(self.store, self._execute, workers=workers, **runner_kwargs)
        self.tools = load_tools()

    # -- execution --------------------------------------------------------------------

    def _execute(
        self, spec: ToolSpec, params: Any, job_id: str, gpu_index: int | None
    ) -> dict[str, Any]:
        workdir = self.jobs_dir / job_id
        workdir.mkdir(parents=True, exist_ok=True)
        ctx = RunContext(
            workdir=workdir,
            gpu_index=gpu_index,
            home=self.home,
            timeout_s=spec.resources.timeout_s,
        )
        started = time.time()
        result = spec.run(params, ctx)
        provenance = {
            "job_id": job_id,
            "tool": spec.name,
            "tool_version": spec.version,
            "bioharbor_version": __version__,
            "params": params.model_dump(mode="json"),
            "gpu": gpu_index,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "started": started,
            "duration_s": round(time.time() - started, 3),
            **ctx.provenance,
        }
        (workdir / "provenance.json").write_text(json.dumps(provenance, indent=2))
        out = result.model_dump(mode="json")
        # Report files relative to the BioHarbor home so they are portable.
        out["files"] = [self._portable(workdir / f) for f in out["files"]]
        if ctx.logs:
            out["logs"] = ctx.logs[-20:]
        return out

    def _portable(self, path: Path) -> str:
        path = path.resolve()  # `workdir / absolute` already yields the absolute path
        return str(path.relative_to(self.home)) if path.is_relative_to(self.home) else str(path)

    def resolve_file(self, rel: str) -> Path:
        """Map a path returned in `files` back to disk, refusing anything outside home."""
        path = (self.home / rel).resolve()
        if not path.is_relative_to(self.home):
            raise InputValidationError(f"path {rel!r} is outside the BioHarbor workspace")
        if not path.is_file():
            raise InputValidationError(
                f"no such file: {rel!r}", hint="use a path from a tool result's `files` list"
            )
        return path

    def validate(self, spec: ToolSpec, raw: dict[str, Any]) -> Any:
        try:
            return spec.params_model.model_validate(raw)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'input'}: {e['msg']}" for e in exc.errors()
            )
            raise InputValidationError(
                f"invalid parameters for {spec.name}: {problems}",
                hint=f"see `describe_tool('{spec.name}')`",
            ) from exc

    def run(
        self, name: str, raw_params: dict[str, Any], wait_s: float | None = None
    ) -> dict[str, Any]:
        """Run a tool. Fast tools answer inline; slow tools become jobs and we wait up to
        `wait_s` seconds before handing back a job_id to poll."""
        spec = get_tool(name)
        try:
            params = self.validate(spec, raw_params)
            if spec.precheck is not None:
                spec.precheck(params, self.home)
        except BioHarborError as exc:
            return {"tool": name, "status": FAILED, "error": exc.to_dict()}

        if not spec.slow:
            return self._run_inline(spec, params).to_dict()

        job_id = self.runner.submit(spec, params)
        job = self.runner.wait(job_id, timeout=wait_s)
        assert job is not None
        out = job.to_dict()
        if not job.done:
            out["hint"] = (
                f"still {job.status}; call get_job('{job_id}') later "
                "(you can keep working meanwhile)"
            )
        return out

    def _run_inline(self, spec: ToolSpec, params: Any) -> Job:
        job = self.store.create(spec.name, params.model_dump(mode="json"))
        self.store.update(job.id, status=RUNNING, started=time.time())
        try:
            result = self._execute(spec, params, job.id, None)
            self.store.update(job.id, status=SUCCEEDED, result=result, finished=time.time())
        except BioHarborError as exc:
            self.store.update(job.id, status=FAILED, error=exc.to_dict(), finished=time.time())
        except Exception as exc:
            self.store.update(
                job.id,
                status=FAILED,
                finished=time.time(),
                error={
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "hint": "unexpected error; please report it",
                    "retryable": False,
                },
            )
        job = self.store.get(job.id)
        assert job is not None
        return job

    # -- introspection ----------------------------------------------------------------

    def describe(self, name: str) -> dict[str, Any]:
        spec = get_tool(name)
        res = spec.resources
        return {
            "name": spec.name,
            "version": spec.version,
            "description": spec.description,
            "runs_as": "background job" if spec.slow else "inline",
            "needs_gpu": res.gpu,
            "input_schema": spec.params_model.model_json_schema(),
        }

    def close(self) -> None:
        self.runner.shutdown()
        self.store.close()

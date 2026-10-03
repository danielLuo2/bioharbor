"""MCP server exposing BioHarbor tools to AI agents."""

from __future__ import annotations

import functools
import inspect
from typing import Annotated, Any

import anyio
from mcp.server.mcpserver import MCPServer
from pydantic import Field

from . import __version__, databases
from . import gpu as gpu_mod
from .errors import BioHarborError
from .harbor import Harbor
from .registry import ToolSpec

INSTRUCTIONS = """\
BioHarbor runs real bioinformatics tools on this machine. Results are compact summaries;
full outputs stay on disk and are listed in `files` (read them with `read_file` only if
needed). Slow tools return a `job_id` if they are not finished within a few seconds:
keep working and check back with `get_job`. Errors include a `hint` on what to try next.
"""

SLOW_WAIT_S = 20.0
READ_LIMIT = 100_000


def _signature_for(spec: ToolSpec) -> inspect.Signature:
    """Flatten the tool's pydantic model into keyword parameters, so the agent sees
    `sequence`, `frame`... instead of a nested `params` object."""
    params = []
    for name, f in spec.params_model.model_fields.items():
        ann = Annotated[f.annotation, Field(description=f.description)]  # type: ignore[valid-type]
        default = (
            inspect.Parameter.empty if f.is_required() else f.get_default(call_default_factory=True)
        )
        params.append(
            inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=default, annotation=ann)
        )
    return inspect.Signature(params, return_annotation=dict[str, Any])


def _register_domain_tool(server: MCPServer, harbor: Harbor, spec: ToolSpec) -> None:
    async def call(**kwargs: Any) -> dict[str, Any]:
        wait = SLOW_WAIT_S if spec.slow else None
        return await anyio.to_thread.run_sync(
            functools.partial(harbor.run, spec.name, kwargs, wait)
        )

    call.__name__ = spec.name
    call.__signature__ = _signature_for(spec)  # type: ignore[attr-defined]
    call.__annotations__ = {}
    desc = spec.description
    if spec.slow:
        desc += "\n\nRuns as a background job; may return a job_id to poll with get_job."
    server.add_tool(call, name=spec.name, description=desc, structured_output=True)


def build_server(harbor: Harbor) -> MCPServer:
    server = MCPServer(name="bioharbor", version=__version__, instructions=INSTRUCTIONS)

    for spec in harbor.tools.values():
        _register_domain_tool(server, harbor, spec)

    @server.tool()
    async def get_job(
        job_id: Annotated[str, Field(description="Job id returned by a slow tool.")],
        wait_seconds: Annotated[
            float, Field(ge=0, le=60, description="Block up to this long for the job to finish.")
        ] = 0,
    ) -> dict[str, Any]:
        """Status and (when finished) result of a background job."""
        job = await anyio.to_thread.run_sync(harbor.runner.wait, job_id, wait_seconds or 0.001)
        if job is None:
            return {"error": "NotFound", "message": f"no job {job_id!r}"}
        return job.to_dict()

    @server.tool()
    def list_jobs(
        limit: Annotated[int, Field(ge=1, le=100)] = 10,
        status: Annotated[
            str | None,
            Field(description="Filter: queued, waiting_gpu, running, succeeded, failed, cancelled"),
        ] = None,
    ) -> dict[str, Any]:
        """Recent jobs, newest first (results omitted; use get_job for details)."""
        jobs = harbor.store.list(limit=limit, status=status)
        return {"jobs": [{k: v for k, v in j.to_dict().items() if k != "result"} for j in jobs]}

    @server.tool()
    def cancel_job(job_id: str) -> dict[str, Any]:
        """Cancel a job that is still queued or waiting for a GPU."""
        ok = harbor.runner.cancel(job_id)
        return {
            "job_id": job_id,
            "cancelled": ok,
            **({} if ok else {"message": "job not found or already started/finished"}),
        }

    @server.tool()
    def describe_tool(name: str) -> dict[str, Any]:
        """Full input schema, version and resource needs of a BioHarbor tool."""
        try:
            return harbor.describe(name)
        except KeyError as exc:
            return {"error": "NotFound", "message": str(exc)}

    @server.tool()
    def list_databases() -> dict[str, Any]:
        """Sequence databases installed for search_homologs, and ones that can be added."""
        installed = [d.to_dict() for d in databases.list_databases(harbor.home)]
        return {
            "installed": installed,
            "available_to_install": {
                k: v
                for k, v in databases.KNOWN_DATABASES.items()
                if k not in {d["name"] for d in installed}
            },
            "message": "the user installs databases with `bioharbor setup-db <name>`",
        }

    @server.tool()
    def gpu_status() -> dict[str, Any]:
        """Live GPU memory and utilisation (includes other users' processes)."""
        gpus = gpu_mod.probe()
        return {
            "gpus": [g.to_dict() for g in gpus],
            "message": f"{len(gpus)} GPU(s) visible" if gpus else "no NVIDIA GPU visible",
        }

    @server.tool()
    def read_file(
        path: Annotated[str, Field(description="A path from a tool result's `files` list.")],
        max_bytes: Annotated[int, Field(ge=1, le=READ_LIMIT)] = 20_000,
    ) -> dict[str, Any]:
        """Read (the start of) a text output file produced by a BioHarbor tool."""
        try:
            p = harbor.resolve_file(path)
        except BioHarborError as exc:
            return exc.to_dict()
        data = p.read_bytes()
        return {
            "path": path,
            "size_bytes": len(data),
            "truncated": len(data) > max_bytes,
            "content": data[:max_bytes].decode("utf-8", errors="replace"),
        }

    return server

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
        job_id: Annotated[
            str, Field(description="The `job_id` returned by a slow tool or listed by list_jobs.")
        ],
        wait_seconds: Annotated[
            # Capped below the ~30 s tool-call timeout some clients (e.g. Cursor) enforce.
            float,
            Field(
                ge=0,
                le=25,
                description="Seconds to wait for the job to finish before returning (0 = "
                "return the current status immediately).",
            ),
        ] = 0,
    ) -> dict[str, Any]:
        """Check a background job and get its result once it has finished.

        Use this after a slow tool (e.g. predict_structure, search_homologs) returned a
        `job_id` instead of a result. Returns `status` (queued, waiting_gpu, running,
        succeeded, failed, cancelled), plus `result` when succeeded or `error` when failed.
        If it is still running, call again later, or pass wait_seconds to block briefly.
        Read-only: checking a job does not change it."""
        job = await anyio.to_thread.run_sync(harbor.runner.wait, job_id, wait_seconds or 0.001)
        if job is None:
            return {"error": "NotFound", "message": f"no job {job_id!r}"}
        return job.to_dict()

    @server.tool()
    def list_jobs(
        limit: Annotated[
            int, Field(ge=1, le=100, description="Maximum number of jobs to return.")
        ] = 10,
        status: Annotated[
            str | None,
            Field(
                description="Only return jobs in this state: queued, waiting_gpu, running, "
                "succeeded, failed or cancelled. Omit for all states."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """List recent background jobs, newest first.

        Use this to find a `job_id` you no longer have, or to see what is queued or running.
        Each entry has job_id, tool, status, gpu and duration_s; results are omitted, so call
        get_job for a job's result. Read-only."""
        jobs = harbor.store.list(limit=limit, status=status)
        return {"jobs": [{k: v for k, v in j.to_dict().items() if k != "result"} for j in jobs]}

    @server.tool()
    def cancel_job(
        job_id: Annotated[
            str,
            Field(description="The `job_id` of the job to cancel (from a slow tool or list_jobs)."),
        ],
    ) -> dict[str, Any]:
        """Cancel a background job that has not started running yet.

        Only jobs whose status is queued or waiting_gpu can be cancelled; a job that is
        already running is not interrupted and will still finish. Returns `cancelled: true`
        on success, or `cancelled: false` with a `message` if the job does not exist or has
        already started or finished. Use list_jobs to see job states first."""
        ok = harbor.runner.cancel(job_id)
        return {
            "job_id": job_id,
            "cancelled": ok,
            **({} if ok else {"message": "job not found or already started/finished"}),
        }

    @server.tool()
    def describe_tool(
        name: Annotated[
            str,
            Field(
                description="Name of an analysis tool: seq_stats, translate_sequence, "
                "find_orfs, search_homologs or predict_structure."
            ),
        ],
    ) -> dict[str, Any]:
        """Explain how an analysis tool runs before calling it.

        Returns the tool's description, version, full JSON input schema (parameter types,
        defaults and limits), whether it needs a GPU, and whether it runs inline or as a
        background job. Use it when unsure about a parameter or after an invalid-parameters
        error. Read-only; returns a NotFound error for unknown names."""
        try:
            return harbor.describe(name)
        except KeyError as exc:
            return {"error": "NotFound", "message": str(exc)}

    @server.tool()
    def list_databases() -> dict[str, Any]:
        """List the sequence databases that search_homologs can search.

        Call this before search_homologs to pick a `database` value. Returns `installed`
        (name, source and sequence count of each ready database) and `available_to_install`
        (known databases such as swissprot, pdb and uniref50 that are not installed yet).
        Agents cannot install databases; ask the user to run `bioharbor setup-db <name>`.
        Read-only."""
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
        """Show live GPU memory and utilisation on this machine.

        Returns, per NVIDIA GPU, its index, name, total and free memory (GB) and utilisation
        (%), counting other users' processes too. Use it to explain why a GPU job is in
        waiting_gpu, or to check capacity before a large predict_structure run. You do not
        need it to schedule jobs: BioHarbor picks a GPU itself. Read-only."""
        gpus = gpu_mod.probe()
        return {
            "gpus": [g.to_dict() for g in gpus],
            "message": f"{len(gpus)} GPU(s) visible" if gpus else "no NVIDIA GPU visible",
        }

    @server.tool()
    def read_file(
        path: Annotated[
            str,
            Field(description="A path exactly as listed in a tool result's `files` list."),
        ],
        max_bytes: Annotated[
            int,
            Field(
                ge=1,
                le=READ_LIMIT,
                description="Return at most this many bytes from the start of the file.",
            ),
        ] = 20_000,
    ) -> dict[str, Any]:
        """Read a text output file written by a BioHarbor tool.

        Tool results are compact summaries; use this only when you need the full output,
        e.g. all ORFs in orfs.faa or all hits of a homology search. Returns `content`, the
        file's `size_bytes`, and `truncated: true` if the file is longer than max_bytes.
        Only files inside the BioHarbor workspace can be read. Read-only."""
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

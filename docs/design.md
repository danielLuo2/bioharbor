# BioHarbor design

## Goal

Let AI agents run real bioinformatics computations safely, reliably and reproducibly on
hardware the user controls, and return results in a form agents can reason about.

**Non-goals:** wrapping databases (use BioMCP & friends alongside), replacing workflow
managers (we export *to* them), model training.

## Layers

```
MCP interface   server.py   flat tool schemas, runtime tools (get_job, read_file, ...)
Service         harbor.py   validation, inline vs background, provenance, workspace
Scheduling      jobs.py     SQLite job store, worker threads, GPU placement + reservations
Hardware        gpu.py      NVML / nvidia-smi probing, pick_gpu policy
Tools           tools/*     @tool functions: params model -> ToolResult
```

The CLI (`cli.py`) calls the same `Harbor` service, so `bioharbor run` is exactly an agent's
tool call.

## Key decisions

**Every call is a job.** Inline tools are recorded too. This gives uniform provenance
(`jobs/<id>/provenance.json`) and is the basis for `export` (v0.3).

**Inline vs background.** Tools declare `slow=True` when they may take more than a few
seconds. The MCP call waits up to 20 s; if the job is not done the agent gets a `job_id`
and a hint to poll `get_job`. This works with every MCP client, independent of
protocol-level long-running task support.

**Polite GPU placement.** Before starting a GPU job the runner probes live state —
including memory used by other people's processes — and picks the GPU with the most free
memory that fits the tool's estimate plus headroom (default 1 GB) and is below a
utilisation cap (default 90 %). Because NVML only reports memory once it is allocated,
the runner also keeps in-process *reservations* so two jobs starting together cannot
both claim the same space. When nothing fits, the job is `waiting_gpu` and retried until
a timeout.

**Agent-shaped results.** `ToolResult` = `summary` (compact JSON), `message`, `files`
(paths relative to the BioHarbor home, readable via `read_file`), `suggestions`. Large
outputs never go into the context window by default.

**Actionable errors.** `BioHarborError` subclasses carry `hint` and `retryable`. Input is
validated before any compute (alphabet, record counts, sizes).

**Restart safety.** Job state lives in SQLite (WAL). When the server starts, jobs left
queued/running by a previous process are marked failed with `retryable: true`.

## Security model (current)

- Only registered tools can run; there is no arbitrary shell tool.
- `read_file` is confined to the BioHarbor home.
- Input size limits in `seqio`.
- HTTP mode has **no auth yet** (v0.4); bind to localhost or use an SSH tunnel.

## Storage layout

```
$BIOHARBOR_HOME (default ~/.bioharbor)
├── bioharbor.sqlite        job table
└── jobs/<job_id>/          outputs + provenance.json
```

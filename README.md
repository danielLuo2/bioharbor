# BioHarbor

**Run real bioinformatics from your AI agent. Reliable, reproducible, on your own GPUs.**

[![CI](https://github.com/danielLuo2/bioharbor/actions/workflows/ci.yml/badge.svg)](https://github.com/danielLuo2/bioharbor/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bioharbor)](https://pypi.org/project/bioharbor/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

> 🚧 **Pre-alpha.** Homology search (MMseqs2) and structure prediction (ESMFold) work;
> a first release is close. See the [roadmap](ROADMAP.md).

BioHarbor is an [MCP](https://modelcontextprotocol.io) server that lets AI agents such as
Claude *execute* bioinformatics tools — not just look things up. Agents ask for an analysis;
BioHarbor validates the input, schedules it on a GPU with room, records exactly how it ran,
and hands back a compact, agent-readable summary.

<!-- TODO: demo GIF goes here -->

## Why another bio MCP server?

Most bio MCP servers wrap **databases** (UniProt, PDB, PubMed…). Use them — BioHarbor
complements them by running the **compute**:

| | Database MCP servers | BioHarbor |
|---|---|---|
| Runs real analyses (search, fold, cluster) | ❌ | ✅ |
| Validates inputs *before* burning GPU time | ❌ | ✅ |
| GPU-aware queue, polite on shared GPUs | ❌ | ✅ |
| Long jobs return a `job_id` instead of timing out | ❌ | ✅ |
| Compact summaries + files on disk (saves tokens) | ❌ | ✅ |
| Provenance for every run, export to a pipeline | ❌ | ✅ (export: planned) |

## Quick start

```bash
pip install bioharbor
bioharbor doctor             # checks Python, GPUs, workspace, tools
bioharbor setup-db swissprot # reference database for search_homologs (needs MMseqs2)
bioharbor install-claude     # prints the config for Claude Desktop / Claude Code
```

For structure prediction on a GPU: `pip install "bioharbor[esmfold]"` — see
[docs/gpu-setup.md](docs/gpu-setup.md) (RTX 50xx needs a CUDA 12.8+ PyTorch).

Claude Code:

```bash
claude mcp add bioharbor -- bioharbor serve
```

Claude Desktop: `bioharbor install-claude --write`, then restart the app.

Then ask your agent something like:

> *Find the longest ORF in this contig, translate it, search Swiss-Prot for homologs and
> predict its structure. Which regions are low confidence?*

### Use it without an agent

Every tool is also a CLI command, with identical behaviour:

```bash
bioharbor tools list
bioharbor run find_orfs sequence=@contig.fa min_aa=100
bioharbor run seq_stats sequence=MKTAYIAKQRQISFVKSHFSRQ
bioharbor jobs
```

### Shared GPU server

Run one BioHarbor on the lab GPU box and point everyone's agent at it:

```bash
bioharbor serve --http --host 0.0.0.0 --port 8765   # MCP endpoint: http://<host>:8765/mcp
```

> ⚠️ Authentication for HTTP mode is on the roadmap; until then expose it only on a
> trusted network or behind an SSH tunnel.

## Tools

| Tool | What it does | Runs |
|---|---|---|
| `seq_stats` | Validate sequences; type, length, GC%, molecular weight | inline |
| `translate_sequence` | DNA/RNA → protein, one or all six frames | inline |
| `find_orfs` | Longest ORFs on both strands, with coordinates | inline |
| `search_homologs` | MMseqs2 search (protein, or translated DNA) vs local DBs | job |
| `predict_structure` | ESMFold structure, pLDDT bands, low-confidence regions, pTM | job (GPU) |
| `scrna_pipeline` | scanpy QC → clustering → markers | *planned* |

Runtime tools: `get_job`, `list_jobs`, `cancel_job`, `describe_tool`, `list_databases`,
`gpu_status`, `read_file`.

## How it works

```
Agent ──MCP──▶ validate input ─▶ inline? ──yes──▶ run ─┐
                                   │ no                  ├─▶ provenance + summary ─▶ Agent
                                   ▼                     │
                     job queue (SQLite) ─▶ GPU placement ┘
                     (waits politely for a GPU with free memory)
```

- **Every call is a job** recorded in SQLite with params, versions, timings and GPU used,
  plus a `provenance.json` next to its outputs.
- **GPU placement** reads live free memory and utilisation (NVML or `nvidia-smi`), keeps
  headroom, and reserves memory for jobs it has started so two jobs never grab the same
  space. Other users' processes are respected.
- **Fail fast**: input, binaries and databases are checked *before* a job is queued, so a
  bad request never waits behind a busy GPU.
- **Results are agent-shaped**: `summary`, `message`, `files`, `suggestions`. Errors carry
  a `hint` and a `retryable` flag.

Details: [docs/design.md](docs/design.md).

## Writing a tool

```python
from pydantic import BaseModel, Field
from bioharbor.registry import Resources, RunContext, tool
from bioharbor.results import ToolResult


class FoldParams(BaseModel):
    sequence: str = Field(..., description="Protein sequence")


@tool(
    version="1",
    slow=True,
    resources=Resources(gpu=True, gpu_mem_gb=lambda p: 4 + len(p.sequence) / 100),
)
def predict_structure(params: FoldParams, ctx: RunContext) -> ToolResult:
    """Predict a protein structure with ESMFold."""
    ...
    return ToolResult(summary={"mean_plddt": 87.1}, files=["model.pdb"])
```

Plugins can ship tools in their own package via the `bioharbor.tools` entry-point group.
See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache-2.0](LICENSE)

# Using BioHarbor with Biomni

[Biomni](https://github.com/snap-stanford/Biomni) is a general-purpose biomedical AI agent
from Stanford. It can load tools from MCP servers with `agent.add_mcp(...)`, so BioHarbor
plugs in as its compute backend: Biomni plans the analysis, BioHarbor runs MMseqs2 and
ESMFold on your GPUs.

BioHarbor runs as its own process, so it can live in a separate environment from Biomni's
large conda environment. Neither has to install the other's dependencies.

## Status

| Biomni version | What works |
|---|---|
| `main` today | Tools are discovered and fast tools (`seq_stats`, `translate_sequence`, `find_orfs`) run, but each result is wrapped in an extra JSON layer (`{"type":"text","text":"..."}`). Biomni also starts a new BioHarbor process for every call, so slow tools lose their state: the ESMFold model is reloaded every time, and a job that returns a `job_id` is stopped when that process exits. Only local servers (`command`) are supported. |
| With [snap-stanford/Biomni#357](https://github.com/snap-stanford/Biomni/pull/357) | One BioHarbor process (or HTTP connection) stays open for the whole session, results come back as BioHarbor's JSON, errors are raised, and remote servers can be given by `url`. |

Until #357 is merged, use its branch in your Biomni checkout:

```bash
cd Biomni
git fetch https://github.com/danielLuo2/biomni fix-mcp-persistent-sessions
git checkout FETCH_HEAD
```

## Option A: BioHarbor on the same machine

Find the absolute path of `bioharbor` in the environment where you installed it
(`which bioharbor`, or `(Get-Command bioharbor).Source` on Windows), then create
`bioharbor_mcp.yaml`:

```yaml
mcp_servers:
  bioharbor:
    command: ["/path/to/envs/bioharbor/bin/bioharbor", "serve"]
    timeout: 900            # seconds per call; slow tools hand back a job_id after ~20 s
    # env:                  # optional; MCP starts the server with a minimal environment
    #   BIOHARBOR_HOME: "/data/bioharbor"
    #   CUDA_VISIBLE_DEVICES: "0,1"
```

## Option B: BioHarbor on a GPU server (needs #357)

Start BioHarbor on the server and open an SSH tunnel from the machine running Biomni, as
in [connect-clients.md](connect-clients.md#mode-c-a-gpu-server-through-an-ssh-tunnel):

```bash
# on the GPU server
bioharbor serve --http --host 127.0.0.1 --port 8765
# where Biomni runs
ssh -N -L 8765:127.0.0.1:8765 you@gpu-server
```

```yaml
mcp_servers:
  bioharbor:
    url: "http://127.0.0.1:8765/mcp"
    timeout: 900
```

The ESMFold model then stays loaded on the server between calls and across Biomni sessions.

## Use it

```python
from biomni.agent import A1

agent = A1(path="./data")
# prints "Discovered 12 tools from bioharbor MCP server"
agent.add_mcp(config_path="bioharbor_mcp.yaml")

agent.go(
    "Find the longest ORF in contig.fa, search Swiss-Prot for homologs of its protein "
    "and predict its structure with BioHarbor. Which regions are low confidence?"
)
```

The tools are also plain Python functions, which is handy for checking the setup:

```python
import json
from mcp_servers.bioharbor import find_orfs, get_job

result = json.loads(find_orfs(sequence=open("contig.fa").read(), min_aa=30))
print(result["result"]["message"])
```

Slow tools (`search_homologs`, `predict_structure`) return within ~20 s. If the work is not
finished by then, the result has `"status": "running"` and a `job_id`; call
`get_job(job_id=...)` until it reports `succeeded`. Biomni's agent picks this up from the
tool descriptions.

## Tested

Through Biomni's `add_mcp` with #357 (mcp 1.12.3, as pinned by Biomni): all 12 BioHarbor
tools are discovered and `find_orfs` returns BioHarbor's JSON, over stdio and over HTTP.
The same check on Biomni `main` shows the extra JSON layer described above.

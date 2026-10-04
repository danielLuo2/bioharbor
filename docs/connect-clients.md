# Connecting Cursor, Codex and Claude to BioHarbor

BioHarbor is a standard MCP server, so any MCP client can use it. There are three ways to
run it; pick by **where the GPU and databases are**:

| Mode | BioHarbor runs on | Use when |
|---|---|---|
| **A. Local, launched by the client** (stdio) | your computer | Everything you need is on this machine. Simplest: the client starts and stops BioHarbor. |
| **B. Local, as an HTTP service** | your computer | You want one long-running BioHarbor (models stay loaded) shared by several clients on this machine. |
| **C. GPU server, via SSH tunnel** | a lab/cloud GPU server | GPUs and databases live on a server and you work from a laptop. |

> ⚠️ HTTP mode has **no authentication yet**. Keep it on `127.0.0.1` and reach a remote
> server through an SSH tunnel (mode C). The tunnel uses your existing SSH port, so no new
> port is opened on the server.

## Mode A: let the client launch BioHarbor

The quickest way is one command, which writes the client's config with the **absolute
path** to `bioharbor` (GUI apps often don't inherit your shell's PATH or virtualenv):

```bash
bioharbor install cursor --write          # ~/.cursor/mcp.json
bioharbor install codex --write           # ~/.codex/config.toml
bioharbor install claude-desktop --write  # Claude Desktop config
claude mcp add bioharbor -- bioharbor serve   # Claude Code
bioharbor install                         # just print every snippet
```

Restart the client afterwards.

**Cursor, by hand:** *Settings → MCP → New*, choose **Command**, then fill in
*Command* = full path to `bioharbor` (`bioharbor.exe` on Windows) and
*Arguments* = `serve`. The README's **Add to Cursor** button pre-fills the same dialog;
replace `bioharbor` with the full path if Cursor cannot find it. To get the full path:

```bash
which bioharbor                    # macOS / Linux
(Get-Command bioharbor).Source     # Windows PowerShell, inside your conda/venv
```

## Mode B: a local HTTP service

```bash
bioharbor serve --http             # listens on http://127.0.0.1:8765/mcp
```

Keep that terminal open, then point the client at the URL:

| Client | Setting |
|---|---|
| Cursor | *Settings → MCP → New → Remote HTTPS*, Server URL `http://127.0.0.1:8765/mcp` |
| Codex | in `~/.codex/config.toml`: `[mcp_servers.bioharbor]` with `url = "http://127.0.0.1:8765/mcp"` |
| Claude Code | `claude mcp add --transport http bioharbor http://127.0.0.1:8765/mcp` |

If Windows Firewall asks, you can decline: local connections don't need it.

## Mode C: a GPU server through an SSH tunnel

On the **server** (inside the environment where BioHarbor, MMseqs2 and the databases are
installed; `tmux`/`screen` keeps it running after you log out):

```bash
bioharbor serve --http --host 127.0.0.1 --port 8765
```

On **your computer**, open a tunnel and leave it running:

```bash
ssh -N -L 8765:127.0.0.1:8765 you@gpu-server
```

Then configure the client exactly as in mode B (`http://127.0.0.1:8765/mcp`). Requests go
through SSH to the server, so `predict_structure` runs on the server's GPUs and
`search_homologs` uses its databases.

## Check that it works

The client should list **bioharbor** as connected with **12 tools**:

![Cursor showing bioharbor connected with 12 tools](images/cursor-connected.png)

Then ask the agent, for example:

| Ask | Expected |
|---|---|
| "Use bioharbor's seq_stats on MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEG" | protein, length 35, molecular weight |
| "Use bioharbor's seq_stats on ACG$T" | an `InputValidationError` with a hint |
| "Search homologs of this protein with bioharbor" | hits, or *mmseqs is not installed* plus install advice if this machine lacks MMseqs2 |
| "Predict the structure of this protein with bioharbor" | pLDDT/pTM, or *no NVIDIA GPU visible* on a machine without a GPU |

Clear errors on a machine without MMseqs2 or a GPU are expected: they show the agent
gets actionable messages instead of a hang.

## Troubleshooting

- **Red dot / `spawn bioharbor ENOENT`** (mode A): the client can't find `bioharbor`. Use
  the absolute path, or run `bioharbor install <client> --write`.
- **The agent ignores BioHarbor's tools:** some clients cap the total number of tools
  across all MCP servers (Cursor has used a 40-tool limit). Disable servers you don't
  need, or name the tool explicitly ("use bioharbor's find_orfs").
- **First structure prediction is slow:** the first call loads the ESMFold weights; under
  `serve` (modes B and C) they stay loaded, so later calls take seconds. Slow tools return
  a `job_id` after ~20 s and the agent polls `get_job`, so no client timeout is hit.
- **Codex times out on a busy machine:** raise `tool_timeout_sec` in its config
  (`bioharbor install codex --write` sets 120 s).

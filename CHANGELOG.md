# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/),
versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-10-05

Connect from Cursor and Codex as well as Claude; friendlier CLI output.

### Added
- `bioharbor install [claude-code|claude-desktop|cursor|codex] [--write]`: shows or writes
  the config for each client (absolute command path, `.bak` backup, re-running replaces
  rather than duplicates). Prints a one-click Cursor install link; the Codex entry sets
  `tool_timeout_sec = 120`. `install-claude` remains as a hidden alias.
- `docs/connect-clients.md`: setting up Cursor, Codex and Claude locally (stdio or HTTP)
  or against a GPU server through an SSH tunnel, with checks and troubleshooting.
- README: "Connect your agent" table for Claude Code, Claude Desktop, Cursor (Add to
  Cursor button) and Codex.
- `bioharbor run --brief`: one status line (time, GPU), the tool's message, suggestions
  and output files instead of raw JSON.
  When a call had to load a model it says so, e.g. `(incl. 145s one-time model load)`.
- `predict_structure` reports `model_load_s` in its summary (0 when the model was warm).
- `docs/demo/demo.tape`: scripted [VHS](https://github.com/charmbracelet/vhs) recording
  of the README demo (contig -> ORF -> Swiss-Prot homologs -> ESMFold structure).

### Changed
- `get_job` blocks for at most 25 s (was 60) to stay under clients' tool-call timeouts.
- `bioharbor tools list` truncates descriptions to the terminal width.
- `find_orfs` suggests the follow-up tools that now exist.
- Each tool's description starts with a one-line summary, so `tools list` fits a terminal.

### Fixed
- Loading ESMFold no longer prints transformers' LOAD REPORT / hub notices, and nothing
  printed during loading can reach stdout, which carries the MCP stream under `serve`.
- Output file paths use forward slashes on Windows too.

## [0.1.0] - 2026-10-03

First usable release.

### Added
- MCP server (stdio and Streamable HTTP) exposing all registered tools with flat input
  schemas, plus `get_job`, `list_jobs`, `cancel_job`, `describe_tool`, `gpu_status` and
  `read_file`.
- CLI: `doctor`, `serve`, `run`, `jobs`, `tools list|describe`, `install-claude`.
- SQLite job store with provenance per run and recovery of interrupted jobs on restart.
- GPU-aware runner: live NVML/nvidia-smi probing, headroom and utilisation limits,
  in-process memory reservations, queueing while no GPU fits.
- Sequence tools: `seq_stats`, `translate_sequence`, `find_orfs`.
- `search_homologs`: MMseqs2 search (protein or translated DNA) with parsed UniProt
  annotations (accession, organism, gene) and next-step suggestions.
- `predict_structure`: ESMFold via transformers with a length-based GPU memory estimate,
  pLDDT bands, low-confidence segments and pTM; peak GPU memory recorded for calibration.
- `bioharbor setup-db` / `databases` and the `list_databases` MCP tool (Swiss-Prot, PDB,
  UniRef50 or your own FASTA, built atomically with source/version metadata).
- Tool `precheck` hooks: invalid input, missing binaries or databases fail before queueing.
- `CUDA_DEVICE_ORDER=PCI_BUS_ID` so the scheduler's GPU index matches PyTorch's.
- `docs/gpu-setup.md`: install, smoke test and memory calibration on RTX 50xx.
- Validated on 2× RTX 5090 (torch 2.11 + CUDA 12.8): ESMFold ubiquitin mean pLDDT 90.5,
  pTM 0.83; peak 7.9 GB vs 9.1 GB estimate; least-busy GPU chosen while shared;
  Swiss-Prot build 32 s, search 11 s.
- `search_homologs` reports `hits_capped` when the per-query hit cap was reached, and
  orders tied hits deterministically.
- `provenance.json` for `predict_structure` splits `model_load_s` from `inference_s`.

### Changed
- Python 3.10 is now supported (many shared lab servers still default to it).
- `.gitattributes` normalises line endings to LF (no spurious diffs on Windows clones).

## [0.0.2] - 2026-10-03
- Name reservation on PyPI.

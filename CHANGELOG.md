# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/),
versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [0.1.5] - 2026-10-10

### Fixed
- The ESMFold memory estimate was too low from ~400 aa up (by 3.4 GB at 600 aa and
  8 GB at 1500 aa), so a fold could be placed on a GPU without enough room. It is now
  fitted on RTX 5090 measurements of the whole process as nvidia-smi sees it, and sits
  0.5–0.8 GB above every measured peak from 50 to 1500 aa.
- A model already loaded on a GPU was counted twice when placing the next job there, so
  long sequences avoided that GPU and loaded a second copy elsewhere. The scheduler now
  credits back memory a tool already holds on a GPU (`Resources.gpu_mem_held`).

### Changed
- `serve` hands cached GPU memory back after every fold, so an idle server holds the
  model (~8.5 GB) instead of its largest job's peak (27.6 GB after a 1500-aa fold).
- ESMFold always uses trunk chunking: no measurable slowdown, and lower peaks from
  500 aa up (600 aa: 17.4 → 13.0 GB).
- On Linux, BioHarbor sets `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` unless you
  set it yourself; it lowers peaks by ~4 GB at 1000–1500 aa, so 1500 aa fits a 32 GB GPU
  with room to spare.
- Clearer tool and parameter descriptions for agents (when to use each tool, what it
  returns, side effects).
- `provenance.json` also records `gpu_peak_reserved_gb` (PyTorch's cache included).

### Added
- `docs/use-with-biomni.md`: using BioHarbor as the compute backend of the Biomni agent,
  locally or on a GPU server, and what works before and after
  [snap-stanford/Biomni#357](https://github.com/snap-stanford/Biomni/pull/357).
- Glama score badge in the README.
- `docs/gpu-setup.md`: measured ESMFold memory and time per length on an RTX 5090.

## [0.1.4] - 2026-10-07

### Added
- `server.json` and an `mcp-name` marker in the README, for listing BioHarbor in the
  official MCP Registry as `io.github.danielLuo2/bioharbor`.
- `docs/connect-clients.md`: Claude Desktop screenshot next to the Codex example.

## [0.1.3] - 2026-10-06

### Changed
- `find_orfs` accepts `min_aa` down to 1 (was 10), so "find the longest ORF" works on
  short sequences without a validation error.

### Added
- When no ORF reaches `min_aa`, `find_orfs` still reports the longest one
  (`summary.longest_below_min`) and suggests the `min_aa` that would list it, so agents
  answer in one call instead of retrying.

## [0.1.2] - 2026-10-05

### Added
- `find_orfs` reports each input's exact length (`inputs[].length_nt`) and each ORF's
  `length_nt` (including the stop codon), so agents quote real numbers instead of
  estimating them.

### Fixed
- `bioharbor install claude-desktop` writes to the Microsoft Store build's config folder
  (`%LOCALAPPDATA%\Packages\Claude_*\LocalCache\Roaming\Claude`) when that build is
  installed; previously Claude never saw the entry.
- Config files that start with a UTF-8 BOM are read correctly.

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

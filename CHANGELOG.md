# Changelog

All notable changes are documented here. Format: [Keep a Changelog](https://keepachangelog.com/),
versioning: [SemVer](https://semver.org/).

## [Unreleased]

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

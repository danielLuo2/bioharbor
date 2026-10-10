# Roadmap

Priorities follow user feedback — open an issue if something matters to you.

## v0.1 — first usable release
- [x] MCP server (stdio + Streamable HTTP), CLI with identical behaviour
- [x] Tool registry with `@tool` decorator and plugin entry points
- [x] Every call recorded as a job (SQLite) with provenance
- [x] Background jobs for slow tools, `get_job` / `cancel_job`, restart recovery
- [x] GPU discovery (NVML / nvidia-smi), polite placement with reservations
- [x] Sequence tools: `seq_stats`, `translate_sequence`, `find_orfs`
- [x] `doctor`, `install` (Claude Code / Claude Desktop / Cursor / Codex)
- [x] `search_homologs` (MMseqs2) + `bioharbor setup-db swissprot`
- [x] `predict_structure` (ESMFold) with length-based GPU memory estimate
- [x] Fail-fast prechecks before queueing
- [x] Validate ESMFold on RTX 5090 (smoke test, GPU placement, Swiss-Prot search)
- [x] Calibrate the ESMFold memory estimate across lengths (RTX 5090, 50–1500 aa)
- [x] PyPI release via trusted publishing (v0.1.0)
- [x] Demo GIF in the README (scripted with VHS)

## v0.2 — reliability
- [ ] Content-addressed result cache
- [ ] Model warm-cache with idle unload (free VRAM for labmates)
- [ ] OOM handling: retry on another GPU / CPU fallback (ESMFold OOM is already reported as retryable)
- [ ] MMseqs2 GPU mode (`--gpu 1`) for large databases
- [ ] Docker runtime for tools with conflicting dependencies
- [ ] `align_sequences` (MAFFT), `scrna_pipeline` (scanpy)

## v0.3 — reproducibility & observability
- [ ] `bioharbor export <session>` → bash / Snakemake / Nextflow + provenance report
- [ ] Prometheus metrics, Grafana dashboard
- [ ] Benchmark v1: bio-agent task suite (success rate, time, tokens)
- [ ] Hugging Face Space demo

## v0.4 — shared servers
- [ ] HTTP auth tokens, per-user quotas
- [ ] OpenTelemetry tracing
- [ ] Plugin SDK docs

## Later
- Boltz-2 / ProteinMPNN / RDKit / docking
- Single-cell foundation model embeddings
- Slurm / Ray backends

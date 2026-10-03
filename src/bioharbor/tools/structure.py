"""Protein structure prediction with ESMFold (via Hugging Face transformers).

Install the model dependencies with ``pip install 'bioharbor[esmfold]'``. Weights
(~8 GB) download from the Hugging Face Hub on first use and stay cached.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..errors import InputValidationError, ResourceUnavailableError, ToolUnavailableError
from ..registry import Resources, RunContext, tool
from ..results import ToolResult
from ..seqio import Record, parse_sequences, require_type

MODEL_ID = "facebook/esmfold_v1"
MAX_LENGTH = 1500
MAX_RECORDS = 20
CHUNK_FROM = 600  # use trunk chunking above this length to bound activation memory


class PredictStructureParams(BaseModel):
    sequence: str = Field(
        ..., description=f"Protein sequence(s), raw or FASTA (≤{MAX_RECORDS} records)."
    )
    num_recycles: int = Field(
        4, ge=0, le=12, description="Recycling iterations; more can help hard targets."
    )


def _max_len(params: BaseModel) -> int:
    try:
        return max(len(r.seq) for r in parse_sequences(params.sequence))  # type: ignore[attr-defined]
    except InputValidationError:
        return 0


def estimate_gpu_gb(length: int) -> float:
    """Heuristic VRAM need: weights (fp16 language model + fp32 trunk) plus pair
    activations growing ~L². Calibrate against `gpu_peak_mem_gb` in provenance."""
    chunked = length > CHUNK_FROM
    return round(9.0 + (6.0 if chunked else 14.0) * (length / 1000) ** 2, 1)


# --- model backend -------------------------------------------------------------------
# A backend takes (sequence, device, num_recycles) and returns (pdb_text, ptm | None).
Backend = Callable[[str, str, int], tuple[str, float | None]]

_models: dict[str, Any] = {}
_model_lock = threading.Lock()
_infer_locks: dict[str, threading.Lock] = {}
_load_seconds: dict[str, float] = {}  # set when a model is (re)loaded, popped by the tool


def _load(device: str) -> Any:
    with _model_lock:
        if device not in _models:
            started = time.monotonic()
            try:
                import torch
                from transformers import EsmForProteinFolding
            except ImportError as exc:
                raise ToolUnavailableError(
                    "ESMFold dependencies are not installed",
                    hint="ask the user to run `pip install 'bioharbor[esmfold]'`",
                ) from exc
            model = EsmForProteinFolding.from_pretrained(MODEL_ID).eval()
            if device.startswith("cuda"):
                model.esm = model.esm.half()  # halves the 3B language model's footprint
                torch.backends.cuda.matmul.allow_tf32 = True
            _models[device] = model.to(device)
            _load_seconds[device] = time.monotonic() - started
        return _models[device]


def esmfold_backend(sequence: str, device: str, num_recycles: int) -> tuple[str, float | None]:
    import torch

    model = _load(device)
    try:
        # One inference at a time per model: recycles/chunking are model-level settings.
        with _infer_locks.setdefault(device, threading.Lock()), torch.no_grad():
            model.trunk.config.max_recycles = num_recycles  # `infer` cannot pass it through
            model.trunk.set_chunk_size(64 if len(sequence) > CHUNK_FROM else None)
            out = model.infer(sequence)
    except torch.cuda.OutOfMemoryError as exc:
        torch.cuda.empty_cache()
        raise ResourceUnavailableError(
            f"GPU out of memory folding {len(sequence)} residues",
            hint="retry later when the GPU is less busy, or fold domains separately",
        ) from exc
    ptm = float(out["ptm"].reshape(-1)[0]) if out.get("ptm") is not None else None
    return model.output_to_pdb(out)[0], ptm


backend: Backend = esmfold_backend


# --- result analysis -----------------------------------------------------------------


def plddt_from_pdb(pdb: str) -> list[float]:
    """Per-residue pLDDT (0-100) from the CA atoms' B-factor column."""
    values = [
        float(line[60:66])
        for line in pdb.splitlines()
        if line.startswith("ATOM") and line[12:16].strip() == "CA"
    ]
    if values and max(values) <= 1.0:  # Hugging Face ESMFold writes 0-1
        values = [v * 100 for v in values]
    return [round(v, 2) for v in values]


def low_confidence_segments(plddt: list[float], cutoff: float = 50, min_len: int = 5):
    """1-based [start, end] runs with pLDDT below `cutoff`, at least `min_len` long."""
    segs, start = [], None
    for i, v in enumerate([*plddt, 101.0]):
        if v < cutoff and start is None:
            start = i
        elif v >= cutoff and start is not None:
            if i - start >= min_len:
                segs.append([start + 1, i])
            start = None
    return segs


def summarize(plddt: list[float]) -> dict[str, Any]:
    n = len(plddt) or 1
    bands = {
        "very_high_gt90": sum(v > 90 for v in plddt) / n,
        "confident_70_90": sum(70 < v <= 90 for v in plddt) / n,
        "low_50_70": sum(50 < v <= 70 for v in plddt) / n,
        "very_low_le50": sum(v <= 50 for v in plddt) / n,
    }
    return {
        "mean_plddt": round(sum(plddt) / n, 1),
        "plddt_fractions": {k: round(v, 3) for k, v in bands.items()},
        "low_confidence_segments": low_confidence_segments(plddt),
    }


def _interpret(s: dict[str, Any]) -> str:
    m = s["mean_plddt"]
    if m >= 90:
        return "very high confidence overall"
    if m >= 70:
        return "confident overall; backbone likely correct"
    if m >= 50:
        return "low confidence; treat as a rough fold"
    return "very low confidence; likely disordered or needs an MSA-based method"


def _check(params: PredictStructureParams) -> list[Record]:
    records = parse_sequences(params.sequence)
    if len(records) > MAX_RECORDS:
        raise InputValidationError(
            f"{len(records)} sequences exceeds {MAX_RECORDS}", hint="split into batches"
        )
    for r in records:
        require_type(r, "protein")
        if len(r.seq) > MAX_LENGTH:
            raise InputValidationError(
                f"{r.id} is {len(r.seq)} aa; ESMFold limit here is {MAX_LENGTH}",
                hint="split into domains (e.g. using homology hits) and fold them separately",
            )
        if set(r.seq) - set("ACDEFGHIKLMNPQRSTVWYX"):
            raise InputValidationError(
                f"{r.id} contains stop/non-standard residues",
                hint="remove '*' and replace B/Z/J/U/O with X",
            )
    return records


def _precheck(params: PredictStructureParams, home: Path) -> None:
    _check(params)


@tool(
    version="1",
    slow=True,
    precheck=_precheck,
    resources=Resources(
        gpu=True, gpu_mem_gb=lambda p: estimate_gpu_gb(_max_len(p)), timeout_s=2 * 3600
    ),
)
def predict_structure(params: PredictStructureParams, ctx: RunContext) -> ToolResult:
    """Predict 3D protein structure(s) with ESMFold on a GPU. Returns per-protein mean
    pLDDT, confidence bands, low-confidence regions and pTM; PDB files are written to
    disk (B-factor column = pLDDT)."""
    records = _check(params)
    device = f"cuda:{ctx.gpu_index}" if ctx.gpu_index is not None else "cpu"

    results, files = [], []
    model_load_s, inference_s = 0.0, []
    for r in records:
        ctx.log(f"folding {r.id} ({len(r.seq)} aa) on {device}")
        t0 = time.monotonic()
        pdb, ptm = backend(r.seq, device, params.num_recycles)
        load_s = _load_seconds.pop(device, 0.0)
        model_load_s += load_s
        inference_s.append(round(time.monotonic() - t0 - load_s, 2))
        path = ctx.workdir / f"{r.id}.pdb"
        path.write_text(pdb)
        files.append(str(path))
        s = summarize(plddt_from_pdb(pdb))
        results.append(
            {
                "id": r.id,
                "length": len(r.seq),
                **s,
                "ptm": round(ptm, 3) if ptm is not None else None,
                "interpretation": _interpret(s),
            }  # fmt: skip
        )

    longest = max(len(r.seq) for r in records)
    ctx.provenance.update(
        {"model": MODEL_ID, "device": device, "gpu_mem_estimate_gb": estimate_gpu_gb(longest)}
    )
    ctx.provenance.update(
        {
            "model_load_s": round(model_load_s, 2),  # 0 when the model was already warm
            "inference_s": inference_s,
        }
    )
    ctx.provenance.update(_peak_memory(device))

    suggestions = []
    if any(x["low_confidence_segments"] for x in results):
        suggestions.append("low-confidence segments may be disordered linkers or tails")
    if any(x["mean_plddt"] < 70 for x in results):
        suggestions.append("run search_homologs: close homologs may have solved structures")
    first = results[0]
    return ToolResult(
        summary={"structures": results},
        message=f"predicted {len(results)} structure(s); {first['id']}: mean pLDDT "
        f"{first['mean_plddt']} ({first['interpretation']})",
        files=files,
        suggestions=suggestions,
    )


def _peak_memory(device: str) -> dict[str, Any]:
    if not device.startswith("cuda"):
        return {}
    try:
        import torch

        peak = torch.cuda.max_memory_allocated(device) / 2**30
        torch.cuda.reset_peak_memory_stats(device)
        return {"gpu_peak_mem_gb": round(peak, 2)}
    except Exception:
        return {}

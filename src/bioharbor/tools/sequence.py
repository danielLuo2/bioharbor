"""Fast, dependency-free sequence tools. These run inline (no job queue)."""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from ..registry import RunContext, tool
from ..results import ToolResult
from ..seqio import parse_sequences, protein_mass, require_type, reverse_complement, translate

SEQ_FIELD = Field(..., description="A raw sequence or FASTA text (one or more records).")
INLINE_LIMIT = 20  # records shown inline; the rest go to a file


class SeqStatsParams(BaseModel):
    sequence: str = SEQ_FIELD


@tool(version="1")
def seq_stats(params: SeqStatsParams, ctx: RunContext) -> ToolResult:
    """Validate sequences: type, length, GC%, molecular weight.

    Reports dna/rna/protein type, length, GC content (nucleotides) or molecular weight
    (proteins) and composition. A cheap first step before heavier tools."""
    records = parse_sequences(params.sequence)
    rows = []
    for rec in records:
        kind = require_type(rec, "dna", "rna", "protein")
        counts = Counter(rec.seq)
        row: dict[str, object] = {"id": rec.id, "type": kind, "length": len(rec.seq)}
        if kind == "protein":
            row["molecular_weight_da"] = protein_mass(rec.seq)
            row["nonstandard_residues"] = sum(v for k, v in counts.items() if k in "XBZJUO*")
        else:
            row["gc_percent"] = round(100 * (counts["G"] + counts["C"]) / len(rec.seq), 2)
            row["ambiguous_bases"] = sum(v for k, v in counts.items() if k not in "ACGTU")
        row["top_residues"] = dict(counts.most_common(5))
        rows.append(row)

    files = []
    if len(rows) > INLINE_LIMIT:
        path = ctx.workdir / "seq_stats.tsv"
        keys = ["id", "type", "length", "gc_percent", "molecular_weight_da"]
        lines = ["\t".join(keys)] + ["\t".join(str(r.get(k, "")) for k in keys) for r in rows]
        path.write_text("\n".join(lines) + "\n")
        files.append(str(path))

    types = Counter(r["type"] for r in rows)
    lengths = [int(r["length"]) for r in rows]
    return ToolResult(
        summary={
            "n_records": len(rows),
            "types": dict(types),
            "length_min": min(lengths),
            "length_max": max(lengths),
            "records": rows[:INLINE_LIMIT],
            "truncated": len(rows) > INLINE_LIMIT,
        },
        message=f"{len(rows)} valid record(s): " + ", ".join(f"{n} {t}" for t, n in types.items()),
        files=files,
    )


class TranslateParams(BaseModel):
    sequence: str = SEQ_FIELD
    frame: Literal[1, 2, 3, -1, -2, -3, "all"] = Field(
        1, description="Reading frame; negative = reverse complement; 'all' = six frames."
    )
    to_stop: bool = Field(False, description="Stop at the first stop codon.")


@tool(version="1")
def translate_sequence(params: TranslateParams, ctx: RunContext) -> ToolResult:
    """Translate DNA/RNA to protein, in one frame or all six.

    Uses the standard genetic code; stop codons appear as '*'."""
    records = parse_sequences(params.sequence)
    frames = [1, 2, 3, -1, -2, -3] if params.frame == "all" else [params.frame]
    out = []
    for rec in records:
        require_type(rec, "dna", "rna")
        seq = rec.seq.replace("U", "T")
        for f in frames:
            src = seq if f > 0 else reverse_complement(seq)
            prot = translate(src[abs(f) - 1 :], to_stop=params.to_stop)
            out.append(
                {
                    "id": rec.id,
                    "frame": f,
                    "length": len(prot),
                    "stops": prot.count("*"),
                    "protein": prot,
                }
            )

    path = ctx.workdir / "translation.faa"
    path.write_text("".join(f">{o['id']}_frame{o['frame']}\n{o['protein']}\n" for o in out))
    shown = [
        {**o, "protein": o["protein"][:300] + ("…" if o["length"] > 300 else "")}
        for o in out[:INLINE_LIMIT]
    ]
    best = min(out, key=lambda o: (o["stops"], -o["length"]))
    return ToolResult(
        summary={"translations": shown, "truncated": len(out) > INLINE_LIMIT},
        message=f"translated {len(records)} record(s) in {len(frames)} frame(s); "
        f"fewest stops: {best['id']} frame {best['frame']} ({best['stops']} stops)",
        files=[str(path)],
    )


class FindOrfsParams(BaseModel):
    sequence: str = SEQ_FIELD
    min_aa: int = Field(50, ge=10, le=10_000, description="Minimum ORF length in amino acids.")
    both_strands: bool = Field(True, description="Also search the reverse complement.")
    top_n: int = Field(10, ge=1, le=100, description="How many of the longest ORFs to return.")


@tool(version="1")
def find_orfs(params: FindOrfsParams, ctx: RunContext) -> ToolResult:
    """Find open reading frames (ATG to stop) on both strands.

    Returns the longest ORFs in DNA/RNA with coordinates (1-based, on the forward strand)
    and writes all ORFs to a FASTA file."""
    records = parse_sequences(params.sequence)
    orfs = []
    for rec in records:
        require_type(rec, "dna", "rna")
        seq = rec.seq.replace("U", "T")
        n = len(seq)
        strands = [(1, seq)] + ([(-1, reverse_complement(seq))] if params.both_strands else [])
        for strand, s in strands:
            for frame in range(3):
                start = None
                for i in range(frame, n - 2, 3):
                    codon = s[i : i + 3]
                    if start is None and codon == "ATG":
                        start = i
                    elif start is not None and codon in ("TAA", "TAG", "TGA"):
                        aa_len = (i - start) // 3
                        if aa_len >= params.min_aa:
                            lo, hi = start + 1, i + 3
                            if strand == -1:
                                lo, hi = n - hi + 1, n - lo + 1
                            orfs.append(
                                {
                                    "id": rec.id,
                                    "strand": "+" if strand == 1 else "-",
                                    "start": lo,
                                    "end": hi,
                                    "length_aa": aa_len,
                                    "protein": translate(s[start:i]),
                                }
                            )
                        start = None

    orfs.sort(key=lambda o: o["length_aa"], reverse=True)
    path = ctx.workdir / "orfs.faa"
    path.write_text(
        "".join(
            f">{o['id']}_orf{k + 1} {o['strand']}{o['start']}-{o['end']} len={o['length_aa']}\n"
            f"{o['protein']}\n"
            for k, o in enumerate(orfs)
        )
    )
    top = [
        {**o, "protein": o["protein"][:120] + ("…" if o["length_aa"] > 120 else "")}
        for o in orfs[: params.top_n]
    ]
    suggestions = []
    if orfs:
        suggestions.append("run search_homologs or predict_structure on the longest ORF protein")
    else:
        suggestions.append(f"no ORFs ≥ {params.min_aa} aa; try a lower min_aa")
    return ToolResult(
        summary={"n_orfs": len(orfs), "top": top},
        message=f"found {len(orfs)} ORF(s) ≥ {params.min_aa} aa"
        + (f"; longest is {orfs[0]['length_aa']} aa" if orfs else ""),
        files=[str(path)],
        suggestions=suggestions,
    )

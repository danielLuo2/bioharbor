"""Homology search with MMseqs2."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..databases import INSTALL_MMSEQS, get_database
from ..errors import InputValidationError
from ..registry import Resources, RunContext, tool
from ..results import ToolResult
from ..runtime import require_binary, run_command
from ..seqio import Record, parse_sequences, require_type

COLUMNS = [
    "query", "target", "fident", "alnlen", "evalue", "bits", "qstart", "qend", "qlen",
    "tstart", "tend", "tlen", "qcov", "tcov", "theader",
]  # fmt: skip
MAX_QUERIES = 1000
INLINE_QUERIES = 5


class SearchHomologsParams(BaseModel):
    sequence: str = Field(
        ..., description="Protein (or DNA, searched translated) sequence(s), raw or FASTA."
    )
    database: str = Field("swissprot", description="Installed database name (see list_databases).")
    top_n: int = Field(10, ge=1, le=100, description="Hits per query shown inline.")
    max_hits: int = Field(
        300, ge=1, le=10_000, description="Max hits per query kept in the output file."
    )
    evalue: float = Field(1e-3, gt=0, le=10, description="E-value cutoff.")
    sensitivity: float = Field(
        5.7, ge=1, le=7.5, description="MMseqs2 -s: 1 fast … 7.5 most sensitive."
    )
    min_coverage: float = Field(
        0.0, ge=0, le=1, description="Minimum alignment coverage of query and target."
    )


_UNIPROT = re.compile(r"^(?:sp|tr)\|([^|]+)\|(\S+)\s*(.*)$")
_TAG = re.compile(r"\s(OS|OX|GN|PE|SV)=")


def parse_header(header: str) -> dict[str, Any]:
    """Pull accession, name, organism and gene out of a UniProt-style FASTA header."""
    out: dict[str, Any] = {}
    m = _UNIPROT.match(header)
    rest = header
    if m:
        out["accession"], out["entry"], rest = m.group(1), m.group(2), m.group(3)
    else:
        first, _, rest = header.partition(" ")
        out["accession"] = first
    parts = _TAG.split(" " + rest)
    out["description"] = parts[0].strip()
    tags = dict(zip(parts[1::2], (p.strip() for p in parts[2::2]), strict=False))
    if "OS" in tags:
        out["organism"] = tags["OS"]
    if "GN" in tags:
        out["gene"] = tags["GN"]
    return out


def parse_hits(text: str) -> dict[str, list[dict[str, Any]]]:
    """Parse MMseqs2 tabular output (COLUMNS order), grouped by query, best first."""
    by_query: dict[str, list[dict[str, Any]]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        vals = line.split("\t")
        row = dict(zip(COLUMNS, vals, strict=False))
        hit = {
            "target": row["target"],
            "identity_pct": round(float(row["fident"]) * 100, 1),
            "evalue": float(row["evalue"]),
            "bits": float(row["bits"]),
            "query_range": [int(row["qstart"]), int(row["qend"])],
            "target_range": [int(row["tstart"]), int(row["tend"])],
            "query_coverage": round(float(row["qcov"]), 3),
            "target_coverage": round(float(row["tcov"]), 3),
            **parse_header(row.get("theader", row["target"])),
        }
        by_query.setdefault(row["query"], []).append(hit)
    for hits in by_query.values():
        # Deterministic order: ties on E-value/bits are common (identical orthologs).
        hits.sort(
            key=lambda h: (
                h["evalue"],
                -h["bits"],
                -h["identity_pct"],
                -h["target_coverage"],
                h["target"],
            )
        )
    return by_query


def _suggest(hits: list[dict[str, Any]], params: SearchHomologsParams) -> list[str]:
    if not hits:
        tips = []
        if params.sensitivity < 7.5:
            tips.append("no hits: retry with sensitivity=7.5")
        if params.database != "uniref50":
            tips.append("search a larger database such as uniref50 (if installed)")
        tips.append("no detectable homologs: predict_structure may still give clues")
        return tips
    best = hits[0]
    if best["identity_pct"] >= 90 and best["query_coverage"] >= 0.8:
        return [
            f"near-identical to {best['accession']}; its function/structure likely apply "
            "(check UniProt / AlphaFold DB)"
        ]
    if best["identity_pct"] < 30:
        return ["only remote homologs (<30% identity): treat annotation transfer with care"]
    return []


def _threads() -> int:
    # Leave cores for labmates on shared machines.
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _check_queries(params: SearchHomologsParams) -> tuple[list[Record], set[str]]:
    records = parse_sequences(params.sequence)
    if len(records) > MAX_QUERIES:
        raise InputValidationError(
            f"{len(records)} queries exceeds {MAX_QUERIES}", hint="split into batches"
        )
    kinds = {require_type(r, "protein", "dna") for r in records}
    if len(kinds) > 1:
        raise InputValidationError(
            "mixed protein and DNA queries", hint="search proteins and DNA in separate calls"
        )
    return records, kinds


def _precheck(params: SearchHomologsParams, home: Path) -> None:
    _check_queries(params)
    require_binary("mmseqs", INSTALL_MMSEQS)
    get_database(home, params.database)


@tool(version="1", slow=True, resources=Resources(timeout_s=6 * 3600), precheck=_precheck)
def search_homologs(params: SearchHomologsParams, ctx: RunContext) -> ToolResult:
    """Find homologous proteins with MMseqs2 (Swiss-Prot, PDB, ...).

    Searches a local database (default Swiss-Prot). Returns the best hits per query with
    identity, E-value, coverage, organism and description; the full hit table is written
    to a TSV file."""
    records, kinds = _check_queries(params)
    mmseqs = require_binary("mmseqs", INSTALL_MMSEQS)
    assert ctx.home is not None
    db = get_database(ctx.home, params.database)

    max_seqs = max(300, params.max_hits)  # MMseqs2 prefilter cap per query
    query = ctx.workdir / "query.fasta"
    query.write_text("".join(f">{r.id}\n{r.seq}\n" for r in records))
    raw = ctx.workdir / "hits.raw.tsv"
    cmd = [
        mmseqs, "easy-search", str(query), str(db.prefix), str(raw), str(ctx.workdir / "tmp"),
        "--format-output", ",".join(COLUMNS),
        "-e", str(params.evalue),
        "-s", str(params.sensitivity),
        "-c", str(params.min_coverage),
        "--max-seqs", str(max_seqs),
        "--threads", str(_threads()),
    ]  # fmt: skip
    if "dna" in kinds:
        cmd += ["--search-type", "2"]  # translated nucleotide vs protein
    run_command(cmd, ctx)

    version = subprocess.run([mmseqs, "version"], capture_output=True, text=True).stdout.strip()
    ctx.provenance.update({"mmseqs_version": version, "database": db.to_dict()})

    by_query = parse_hits(raw.read_text())
    out = ctx.workdir / "hits.tsv"
    keys = ["query", "accession", "entry", "description", "organism", "identity_pct",
            "evalue", "bits", "query_coverage", "target_coverage"]  # fmt: skip
    lines = ["\t".join(keys)]
    for q, hits in by_query.items():
        for h in hits[: params.max_hits]:
            lines.append("\t".join(str({**h, "query": q}.get(k, "")) for k in keys))
    out.write_text("\n".join(lines) + "\n")
    raw.unlink()

    queries = []
    for r in records[:INLINE_QUERIES]:
        hits = by_query.get(r.id, [])
        capped = len(hits) >= max_seqs
        suggestions = _suggest(hits, params)
        if capped:
            suggestions.append(
                f"hit list reached the cap of {max_seqs}; more distant homologs may be "
                "missing (raise max_hits, e.g. 2000, to see them)"
            )
        queries.append(
            {
                "query": r.id,
                "n_hits": len(hits),
                "hits_capped": capped,
                "top_hits": hits[: params.top_n],
                "suggestions": suggestions,
            }
        )
    n_with = sum(1 for r in records if by_query.get(r.id))
    first = queries[0]["top_hits"][0] if queries[0]["top_hits"] else None
    message = f"{n_with}/{len(records)} queries have hits in {db.name}"
    if first:
        message += (
            f"; best for {records[0].id}: {first.get('description') or first['target']}"
            f" ({first['identity_pct']}% id, E={first['evalue']:.1e})"
        )
    return ToolResult(
        summary={
            "database": db.name,
            "queries": queries,
            "truncated_queries": len(records) > INLINE_QUERIES,
        },
        message=message,
        files=[str(out)],
        suggestions=queries[0]["suggestions"] if len(queries) == 1 else [],
    )

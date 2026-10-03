"""Sequence parsing and validation, with errors an agent can act on."""

from __future__ import annotations

from dataclasses import dataclass

from .errors import InputValidationError

DNA = set("ACGT")
RNA = set("ACGU")
NUC_IUPAC = set("ACGTURYKMSWBDHVN")
PROTEIN = set("ACDEFGHIKLMNPQRSTVWY")
PROTEIN_EXT = PROTEIN | set("XBZJUO*")

MAX_RECORDS = 10_000
MAX_TOTAL_RESIDUES = 50_000_000


@dataclass(frozen=True)
class Record:
    id: str
    seq: str
    description: str = ""


def parse_sequences(text: str) -> list[Record]:
    """Accept FASTA text or a bare sequence. Whitespace and digits are ignored, case is
    normalised to upper."""
    text = text.strip()
    if not text:
        raise InputValidationError("empty sequence input", hint="pass a raw sequence or FASTA text")
    records: list[Record] = []
    if not text.startswith(">"):
        records.append(Record("seq1", _clean(text)))
    else:
        header, chunks = None, []
        for line in text.splitlines():
            if line.startswith(">"):
                if header is not None:
                    records.append(_make(header, chunks, len(records)))
                header, chunks = line[1:].strip(), []
            else:
                chunks.append(line)
        records.append(_make(header or "", chunks, len(records)))
    if len(records) > MAX_RECORDS:
        raise InputValidationError(
            f"{len(records)} records exceeds limit of {MAX_RECORDS}",
            hint="split the input into smaller batches",
        )
    if sum(len(r.seq) for r in records) > MAX_TOTAL_RESIDUES:
        raise InputValidationError(
            "input too large", hint=f"keep total residues under {MAX_TOTAL_RESIDUES:,}"
        )
    empty = [r.id for r in records if not r.seq]
    if empty:
        raise InputValidationError(f"empty sequence for record(s): {', '.join(empty[:5])}")
    return records


def _clean(s: str) -> str:
    return "".join(c for c in s.upper() if not c.isspace() and not c.isdigit())


def _make(header: str, chunks: list[str], n: int) -> Record:
    rid, _, desc = header.partition(" ")
    return Record(rid or f"seq{n + 1}", _clean("".join(chunks)), desc)


def detect_type(seq: str) -> str:
    """Return 'dna', 'rna' or 'protein'. Raises on characters valid for neither."""
    chars = set(seq)
    if chars <= NUC_IUPAC:
        # N runs are common in assemblies, so judge on the non-N part.
        unknown = seq.count("N")
        core = sum(seq.count(c) for c in "ACGTU")
        if core + unknown >= 0.9 * len(seq) and core >= 0.5 * (len(seq) - unknown):
            return "rna" if "U" in chars and "T" not in chars else "dna"
    if chars <= PROTEIN_EXT:
        return "protein"
    bad = sorted(chars - PROTEIN_EXT - NUC_IUPAC)
    pos = next(i for i, c in enumerate(seq) if c in bad)
    raise InputValidationError(
        f"invalid character(s) {''.join(bad)!r} (first at position {pos + 1})",
        hint="sequences must use IUPAC nucleotide or amino-acid letters",
    )


def require_type(rec: Record, *allowed: str) -> str:
    kind = detect_type(rec.seq)
    if kind not in allowed:
        raise InputValidationError(
            f"record {rec.id!r} looks like {kind}, but this tool needs {' or '.join(allowed)}",
            hint="check you passed the right sequence (protein vs nucleotide)",
        )
    return kind


_BASES = "TCAG"
_AA = "FFLLSSSSYY**CC*WLLLLPPPPHHQQRRRRIIIMTTTTNNKKSSRRVVVVAAAADDEEGGGG"
CODON_TABLE = {
    a + b + c: _AA[16 * i + 4 * j + k]
    for i, a in enumerate(_BASES)
    for j, b in enumerate(_BASES)
    for k, c in enumerate(_BASES)
}
_COMPLEMENT = str.maketrans("ACGTURYKMSWBDHVN", "TGCAAYRMKSWVHDBN")


def reverse_complement(seq: str) -> str:
    return seq.translate(_COMPLEMENT)[::-1]


def translate(seq: str, to_stop: bool = False) -> str:
    """Translate a DNA/RNA sequence (frame 1) with the standard code. Codons with
    ambiguous bases become 'X'."""
    seq = seq.replace("U", "T")
    out = []
    for i in range(0, len(seq) - 2, 3):
        aa = CODON_TABLE.get(seq[i : i + 3], "X")
        if aa == "*" and to_stop:
            break
        out.append(aa)
    return "".join(out)


# Average residue masses (Da) of amino acids in a peptide chain, plus one water.
_AA_MASS = {
    "A": 71.0788,
    "R": 156.1875,
    "N": 114.1038,
    "D": 115.0886,
    "C": 103.1388,
    "E": 129.1155,
    "Q": 128.1307,
    "G": 57.0519,
    "H": 137.1411,
    "I": 113.1594,
    "L": 113.1594,
    "K": 128.1741,
    "M": 131.1926,
    "F": 147.1766,
    "P": 97.1167,
    "S": 87.0782,
    "T": 101.1051,
    "W": 186.2132,
    "Y": 163.1760,
    "V": 99.1326,
}
_WATER = 18.01528


def protein_mass(seq: str) -> float | None:
    """Average molecular weight in Da; None if the sequence has non-standard residues."""
    if not set(seq) <= set(_AA_MASS):
        return None
    return round(sum(_AA_MASS[c] for c in seq) + _WATER, 2)

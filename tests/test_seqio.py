import pytest

from bioharbor.errors import InputValidationError
from bioharbor.seqio import (
    detect_type,
    parse_sequences,
    protein_mass,
    reverse_complement,
    translate,
)


def test_parse_bare_and_fasta():
    assert parse_sequences("acgt acgt\n12")[0].seq == "ACGTACGT"
    recs = parse_sequences(">a desc here\nMKT\nAYI\n>b\nACGT\n")
    assert [(r.id, r.seq, r.description) for r in recs] == [
        ("a", "MKTAYI", "desc here"),
        ("b", "ACGT", ""),
    ]


@pytest.mark.parametrize("text", ["", "   ", ">a\n\n>b\nACGT"])
def test_parse_rejects_empty(text):
    with pytest.raises(InputValidationError):
        parse_sequences(text)


@pytest.mark.parametrize(
    "seq,kind",
    [
        ("ACGTACGTNN", "dna"),
        ("ACGTNNNNNNNNNNACGT", "dna"),  # assembly gap
        ("ACGUACGU", "rna"),
        ("MKTAYIAKQR", "protein"),
        ("MKT", "protein"),  # all IUPAC nucleotide letters, but not nucleotide-like
    ],
)
def test_detect_type(seq, kind):
    assert detect_type(seq) == kind


def test_detect_type_reports_bad_char_position():
    with pytest.raises(InputValidationError, match="position 4"):
        detect_type("ACG!T")


def test_reverse_complement_and_translate():
    assert reverse_complement("ATGCN") == "NGCAT"
    assert translate("ATGGCCTAAGGG") == "MA*G"
    assert translate("ATGGCCTAAGGG", to_stop=True) == "MA"
    assert translate("AUGNNN") == "MX"


def test_protein_mass():
    assert protein_mass("G") == pytest.approx(75.07, abs=0.01)  # glycine
    assert protein_mass("MKX") is None

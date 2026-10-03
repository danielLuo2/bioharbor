import os
import shutil
from pathlib import Path

import pytest

from bioharbor.databases import get_database, setup_database
from bioharbor.errors import ToolUnavailableError
from bioharbor.tools.homology import parse_header, parse_hits

DATA = Path(__file__).parent / "data"
HBA = (
    "MVLSPADKTNVKAAWGKVGAHAGEYGAEALERMFLSFPTTKTYFPHFDLSHGSAQVKGHGKKVADALTNAVAHVDDMPNALSALSDLHAHK"
    "LRVDPVNFKLLSHCLLVTLAAHLPAEFTPAVHASLDKFLASVSTVLTSKYR"
)
MMSEQS = os.environ.get("BIOHARBOR_MMSEQS") or shutil.which("mmseqs")
needs_mmseqs = pytest.mark.skipif(not MMSEQS, reason="mmseqs not installed")


def test_parse_uniprot_header():
    h = parse_header("sp|P69905|HBA_HUMAN Hemoglobin subunit alpha OS=Homo sapiens OX=9606 GN=HBA1")
    assert h == {
        "accession": "P69905",
        "entry": "HBA_HUMAN",
        "description": "Hemoglobin subunit alpha",
        "organism": "Homo sapiens",
        "gene": "HBA1",
    }
    assert parse_header("my_protein some description") == {
        "accession": "my_protein",
        "description": "some description",
    }


def test_parse_hits_groups_and_sorts():
    line = "q{n}\tT{t}\t{f}\t100\t{e}\t50\t1\t100\t100\t1\t100\t100\t1.0\t1.0\tT{t} desc"
    text = "\n".join(
        [
            line.format(n=1, t=1, f="0.5", e="1e-5"),
            line.format(n=1, t=2, f="0.9", e="1e-20"),
            line.format(n=2, t=3, f="0.3", e="1e-3"),
        ]
    )
    hits = parse_hits(text)
    assert [h["target"] for h in hits["q1"]] == ["T2", "T1"]
    assert hits["q1"][0]["identity_pct"] == 90.0
    assert len(hits["q2"]) == 1


def test_missing_database_has_actionable_hint(tmp_path):
    with pytest.raises(ToolUnavailableError) as exc:
        get_database(tmp_path, "swissprot")
    assert "bioharbor setup-db swissprot" in exc.value.hint


def test_missing_mmseqs_reported(harbor, monkeypatch):
    monkeypatch.setenv("BIOHARBOR_MMSEQS", str(harbor.home / "no-such-binary"))
    out = harbor.run("search_homologs", {"sequence": HBA}, wait_s=30)
    assert out["status"] == "failed"
    assert out["error"]["error"] == "ToolUnavailableError"
    assert "mmseqs2" in out["error"]["hint"]
    assert "job_id" not in out  # failed in precheck, never queued


@pytest.fixture
def mini_db(harbor):
    import subprocess

    return setup_database(
        harbor.home,
        "mini",
        MMSEQS,
        from_fasta=DATA / "mini_db.fasta",
        run=lambda cmd: subprocess.run(cmd, check=True, capture_output=True),
    )


@needs_mmseqs
def test_search_finds_homologs(harbor, mini_db):
    query = HBA[:60] + "W" + HBA[61:]  # one substitution
    out = harbor.run(
        "search_homologs",
        {"sequence": query, "database": "mini", "sensitivity": 7.5},
        wait_s=120,
    )
    assert out["status"] == "succeeded", out
    q = out["result"]["summary"]["queries"][0]
    assert [h["entry"] for h in q["top_hits"]] == ["HBA_HUMAN", "HBB_HUMAN"]
    assert q["hits_capped"] is False
    assert 99 <= q["top_hits"][0]["identity_pct"] < 100
    assert "near-identical" in q["suggestions"][0]
    prov = (harbor.home / f"jobs/{out['job_id']}/provenance.json").read_text()
    assert '"mmseqs_version"' in prov and '"source": "fasta:mini_db.fasta"' in prov
    table = harbor.resolve_file(out["result"]["files"][0]).read_text().splitlines()
    assert table[0].startswith("query\taccession") and len(table) == 3


@needs_mmseqs
def test_translated_dna_search(harbor, mini_db):
    from bioharbor.seqio import CODON_TABLE

    back = {aa: codon for codon, aa in CODON_TABLE.items()}
    dna = "".join(back[aa] for aa in HBA)
    out = harbor.run("search_homologs", {"sequence": dna, "database": "mini"}, wait_s=120)
    assert out["status"] == "succeeded", out
    assert out["result"]["summary"]["queries"][0]["top_hits"][0]["entry"] == "HBA_HUMAN"


@needs_mmseqs
def test_no_hits_suggests_next_steps(harbor, mini_db):
    out = harbor.run(
        "search_homologs",
        {"sequence": "WWWWPPPPWWWWPPPPWWWWPPPPWWWW", "database": "mini", "sensitivity": 4},
        wait_s=120,
    )
    q = out["result"]["summary"]["queries"][0]
    assert q["n_hits"] == 0
    assert any("sensitivity=7.5" in s for s in q["suggestions"])

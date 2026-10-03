from bioharbor.seqio import reverse_complement

ORF = "ATG" + "GCT" * 60 + "TAA"  # 60 aa after Met -> length_aa 61


def ok(out):
    assert out["status"] == "succeeded", out
    return out["result"]


def test_seq_stats(harbor):
    res = ok(harbor.run("seq_stats", {"sequence": ">p\nMKTAYIAK\n>d\nGGCCAATT"}))
    recs = {r["id"]: r for r in res["summary"]["records"]}
    assert recs["p"]["type"] == "protein" and recs["p"]["molecular_weight_da"] > 0
    assert recs["d"]["gc_percent"] == 50.0


def test_seq_stats_many_records_go_to_file(harbor):
    fasta = "".join(f">s{i}\nACGT\n" for i in range(30))
    res = ok(harbor.run("seq_stats", {"sequence": fasta}))
    assert res["summary"]["truncated"] is True
    assert len(res["summary"]["records"]) == 20
    assert res["files"][0].startswith("jobs/") and "\\" not in res["files"][0]
    path = harbor.resolve_file(res["files"][0])
    assert path.read_text().count("\n") == 31


def test_invalid_input_returns_actionable_error(harbor):
    out = harbor.run("seq_stats", {"sequence": "ACG$T"})
    assert out["status"] == "failed"
    assert out["error"]["error"] == "InputValidationError"
    assert out["error"]["hint"]


def test_missing_param_is_validation_error(harbor):
    out = harbor.run("seq_stats", {})
    assert out["error"]["error"] == "InputValidationError"


def test_translate_rejects_protein(harbor):
    out = harbor.run("translate_sequence", {"sequence": "MKTAYIAKQRQ"})
    assert out["status"] == "failed" and "protein" in out["error"]["message"]


def test_translate_six_frames(harbor):
    res = ok(harbor.run("translate_sequence", {"sequence": ORF, "frame": "all"}))
    tr = res["summary"]["translations"]
    assert [t["frame"] for t in tr] == [1, 2, 3, -1, -2, -3]
    assert tr[0]["protein"] == "M" + "A" * 60 + "*"


def test_find_orfs_both_strands(harbor):
    seq = "CC" + ORF + "GGGG" + reverse_complement(ORF) + "C"
    res = ok(harbor.run("find_orfs", {"sequence": seq, "min_aa": 50}))
    top = res["summary"]["top"]
    assert res["summary"]["n_orfs"] == 2
    fwd = next(o for o in top if o["strand"] == "+")
    rev = next(o for o in top if o["strand"] == "-")
    assert (fwd["start"], fwd["end"]) == (3, 2 + len(ORF))
    rc_start = 2 + len(ORF) + 4 + 1
    assert (rev["start"], rev["end"]) == (rc_start, rc_start + len(ORF) - 1)
    assert fwd["length_aa"] == rev["length_aa"] == 61


def test_runs_are_recorded_with_provenance(harbor):
    out = harbor.run("seq_stats", {"sequence": "ACGT"})
    job = harbor.store.get(out["job_id"])
    assert job.status == "succeeded" and job.params == {"sequence": "ACGT"}
    prov = harbor.jobs_dir / out["job_id"] / "provenance.json"
    assert '"tool": "seq_stats"' in prov.read_text()


def test_resolve_file_refuses_escape(harbor):
    import pytest

    from bioharbor.errors import InputValidationError

    with pytest.raises(InputValidationError):
        harbor.resolve_file("../../etc/passwd")


def test_brief_output(harbor):
    from bioharbor.cli import format_brief

    ok_out = harbor.run("find_orfs", {"sequence": ORF})
    text = format_brief(ok_out)
    assert text.startswith("✓ find_orfs succeeded in ")
    assert "longest is 61 aa" in text and "file: jobs/" in text
    bad = format_brief(harbor.run("seq_stats", {"sequence": "AC$GT"}))
    assert bad.startswith("✗ seq_stats failed") and "hint:" in bad

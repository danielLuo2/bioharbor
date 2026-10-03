import json

import pytest

from bioharbor.gpu import GPUInfo
from bioharbor.harbor import Harbor
from bioharbor.tools import structure
from bioharbor.tools.structure import (
    estimate_gpu_gb,
    low_confidence_segments,
    plddt_from_pdb,
    summarize,
)

UBQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"


def fake_pdb(bfactors):
    lines = []
    for i, b in enumerate(bfactors, start=1):
        for name in (" N  ", " CA ", " C  "):
            lines.append(
                f"ATOM  {i:5d} {name} ALA A{i:4d}    "
                f"{0.0:8.3f}{0.0:8.3f}{0.0:8.3f}{1.0:6.2f}{b:6.2f}           C"
            )
    return "\n".join(lines) + "\nEND\n"


def test_plddt_parsing_scales_hf_output():
    assert plddt_from_pdb(fake_pdb([0.9, 0.5])) == [90.0, 50.0]
    assert plddt_from_pdb(fake_pdb([88.0, 42.5])) == [88.0, 42.5]


def test_low_confidence_segments():
    p = [90] * 3 + [30] * 5 + [90] * 2 + [40] * 2 + [20] * 6
    assert low_confidence_segments(p) == [[4, 8], [11, 18]]


def test_summarize_bands():
    s = summarize([95, 80, 60, 40])
    assert s["mean_plddt"] == 68.8
    assert s["plddt_fractions"] == {
        "very_high_gt90": 0.25,
        "confident_70_90": 0.25,
        "low_50_70": 0.25,
        "very_low_le50": 0.25,
    }


def test_gpu_estimate_grows_with_length():
    assert estimate_gpu_gb(100) < estimate_gpu_gb(500) < estimate_gpu_gb(1500) < 32


@pytest.fixture
def gpu_harbor(tmp_path, monkeypatch):
    calls = []

    def backend(seq, device, recycles):
        calls.append((len(seq), device, recycles))
        return fake_pdb([0.92] * (len(seq) - 10) + [0.3] * 10), 0.81

    monkeypatch.setattr(structure, "backend", backend)
    h = Harbor(
        home=tmp_path / "h",
        gpu_probe=lambda: [GPUInfo(0, "fake", 32, 4, 0), GPUInfo(1, "fake", 32, 30, 5)],
        gpu_poll_s=0.01,
    )
    h.calls = calls
    yield h
    h.close()


def test_predict_structure_end_to_end(gpu_harbor):
    out = gpu_harbor.run(
        "predict_structure", {"sequence": f">ubq\n{UBQ}", "num_recycles": 2}, wait_s=10
    )
    assert out["status"] == "succeeded", out
    assert out["gpu"] == 1  # GPU 0 has only 4 GB free
    assert gpu_harbor.calls == [(76, "cuda:1", 2)]
    s = out["result"]["summary"]["structures"][0]
    assert s["ptm"] == 0.81 and s["low_confidence_segments"] == [[67, 76]]
    assert out["result"]["files"][0].endswith("ubq.pdb")
    assert any("disordered" in x for x in out["result"]["suggestions"])
    prov = json.loads((gpu_harbor.home / f"jobs/{out['job_id']}/provenance.json").read_text())
    assert prov["model_load_s"] == 0.0 and len(prov["inference_s"]) == 1


@pytest.mark.parametrize(
    "seq,msg",
    [
        ("ACGTACGTACGTACGT", "needs protein"),
        ("M" * 1501, "limit"),
        ("MKT*AYI", "stop"),
    ],
)
def test_predict_structure_rejects_bad_input_before_gpu(gpu_harbor, seq, msg):
    out = gpu_harbor.run("predict_structure", {"sequence": seq}, wait_s=10)
    assert out["status"] == "failed" and msg in out["error"]["message"]
    assert "job_id" not in out  # rejected before queueing
    assert gpu_harbor.calls == []


def test_bad_input_reported_even_without_gpu(tmp_path):
    h = Harbor(home=tmp_path / "h", gpu_probe=lambda: [])
    try:
        out = h.run("predict_structure", {"sequence": "MKT*"}, wait_s=5)
    finally:
        h.close()
    assert out["error"]["error"] == "InputValidationError"

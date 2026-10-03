import threading
import time

from pydantic import BaseModel

from bioharbor.gpu import GPUInfo
from bioharbor.harbor import Harbor
from bioharbor.jobs import JobStore
from bioharbor.registry import Resources, RunContext, tool
from bioharbor.results import ToolResult

release = threading.Event()


class GpuParams(BaseModel):
    gb: float = 10


@tool("_test_gpu_job", resources=Resources(gpu=True, gpu_mem_gb=lambda p: p.gb), slow=True)
def _test_gpu_job(params: GpuParams, ctx: RunContext) -> ToolResult:
    release.wait(5)
    return ToolResult(summary={"gpu": ctx.gpu_index})


def make(tmp_path, gpus):
    return Harbor(home=tmp_path / "h", workers=3, gpu_probe=lambda: gpus, gpu_poll_s=0.02)


def test_slow_job_returns_job_id_then_result(tmp_path):
    release.clear()
    h = make(tmp_path, [GPUInfo(0, "g", 32, 30, 0)])
    try:
        out = h.run("_test_gpu_job", {"gb": 4}, wait_s=0.1)
        assert out["status"] == "running" and "get_job" in out["hint"]
        release.set()
        job = h.runner.wait(out["job_id"], timeout=5)
        assert job.status == "succeeded" and job.result["summary"] == {"gpu": 0}
    finally:
        release.set()
        h.close()


def test_reservations_prevent_overbooking(tmp_path):
    """Two 12 GB jobs on a GPU with 20 GB free: the second must wait for the first."""
    release.clear()
    h = make(tmp_path, [GPUInfo(0, "g", 32, 20, 0)])
    try:
        a = h.run("_test_gpu_job", {"gb": 12}, wait_s=0.1)
        b = h.run("_test_gpu_job", {"gb": 12}, wait_s=0.2)
        assert a["status"] == "running"
        assert b["status"] == "waiting_gpu"
        assert h.runner.cancel(b["job_id"]) is True
        release.set()
        assert h.runner.wait(a["job_id"], 5).status == "succeeded"
        assert h.runner.wait(b["job_id"], 5).status == "cancelled"
    finally:
        release.set()
        h.close()


def test_no_gpu_fails_with_message(tmp_path):
    h = make(tmp_path, [])
    try:
        out = h.run("_test_gpu_job", {}, wait_s=5)
        assert out["status"] == "failed" and "no NVIDIA GPU" in out["error"]["message"]
    finally:
        h.close()


def test_recover_interrupted(tmp_path):
    store = JobStore(tmp_path / "db.sqlite")
    job = store.create("x", {})
    store.update(job.id, status="running", started=time.time())
    assert store.recover_interrupted() == 1
    got = store.get(job.id)
    assert got.status == "failed" and got.error["retryable"] is True

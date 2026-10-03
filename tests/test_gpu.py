from bioharbor.gpu import GPUInfo, parse_nvidia_smi, pick_gpu


def test_parse_nvidia_smi():
    out = (
        "0, NVIDIA GeForce RTX 5090, 32607, 30000, 3\n1, NVIDIA GeForce RTX 5090, 32607, 512, 99\n"
    )
    gpus = parse_nvidia_smi(out)
    assert gpus[0] == GPUInfo(0, "NVIDIA GeForce RTX 5090", 31.84, 29.3, 3)
    assert gpus[1].util_pct == 99


def test_pick_gpu_prefers_most_free_and_respects_limits():
    gpus = [GPUInfo(0, "a", 32, 10, 10), GPUInfo(1, "b", 32, 20, 10), GPUInfo(2, "c", 32, 30, 95)]
    assert pick_gpu(gpus, 8).index == 1  # gpu 2 is too busy
    assert pick_gpu(gpus, 19.5) is None  # 20 GB free minus 1 GB headroom is not enough
    assert pick_gpu(gpus, 8, allowed={0}).index == 0

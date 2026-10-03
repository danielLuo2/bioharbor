"""GPU discovery and placement.

Reads live state through NVML (``pip install bioharbor[gpu]``) or falls back to
``nvidia-smi``. Memory used by *other* processes counts, so BioHarbor stays polite on
GPUs shared with labmates.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class GPUInfo:
    index: int
    name: str
    mem_total_gb: float
    mem_free_gb: float
    util_pct: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _probe_nvml() -> list[GPUInfo] | None:
    try:
        import pynvml
    except ImportError:
        return None
    try:
        pynvml.nvmlInit()
    except pynvml.NVMLError:
        return None
    try:
        gpus = []
        for i in range(pynvml.nvmlDeviceGetCount()):
            h = pynvml.nvmlDeviceGetHandleByIndex(i)
            mem = pynvml.nvmlDeviceGetMemoryInfo(h)
            util = pynvml.nvmlDeviceGetUtilizationRates(h)
            name = pynvml.nvmlDeviceGetName(h)
            gpus.append(
                GPUInfo(
                    index=i,
                    name=name.decode() if isinstance(name, bytes) else name,
                    mem_total_gb=round(mem.total / 2**30, 2),
                    mem_free_gb=round(mem.free / 2**30, 2),
                    util_pct=int(util.gpu),
                )
            )
        return gpus
    finally:
        pynvml.nvmlShutdown()


def parse_nvidia_smi(output: str) -> list[GPUInfo]:
    """Parse `nvidia-smi --query-gpu=index,name,memory.total,memory.free,utilization.gpu
    --format=csv,noheader,nounits`."""
    gpus = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 5:
            continue
        idx, name, total, free, util = parts
        gpus.append(
            GPUInfo(
                index=int(idx),
                name=name,
                mem_total_gb=round(float(total) / 1024, 2),
                mem_free_gb=round(float(free) / 1024, 2),
                util_pct=int(float(util)) if util.replace(".", "").isdigit() else 0,
            )
        )
    return gpus


def _probe_smi() -> list[GPUInfo]:
    if not shutil.which("nvidia-smi"):
        return []
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,memory.free,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return []
    return parse_nvidia_smi(out)


def probe() -> list[GPUInfo]:
    """Current state of every visible NVIDIA GPU (empty list if none)."""
    gpus = _probe_nvml()
    return gpus if gpus is not None else _probe_smi()


def pick_gpu(
    gpus: list[GPUInfo],
    need_gb: float,
    *,
    headroom_gb: float = 1.0,
    max_util_pct: int = 90,
    allowed: set[int] | None = None,
) -> GPUInfo | None:
    """Choose the GPU with the most free memory that fits `need_gb` plus headroom.

    Returns None when nothing fits right now; the caller should queue and retry.
    """
    fits = [
        g
        for g in gpus
        if (allowed is None or g.index in allowed)
        and g.mem_free_gb >= need_gb + headroom_gb
        and g.util_pct <= max_util_pct
    ]
    return max(fits, key=lambda g: g.mem_free_gb, default=None)

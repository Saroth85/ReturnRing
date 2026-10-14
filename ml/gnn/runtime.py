"""Device, seeds, backend and timers of the GraphSAGE jobs (US-202; spec 12 'Tempi distinti').

- Device: DEVICE=cpu|gpu (or --device). On ROCm builds of PyTorch the HIP device is exposed through
  the torch.cuda API, so gpu is torch.device("cuda"); a GPU run without a visible GPU is refused
  (GpuUnavailable), never silently moved to the CPU.
- Backend: DGL when importable (the trainer-gnn image, rocm/dgl), else the pure-PyTorch layers of
  ml.gnn.model, the same model. --backend dgl|torch forces one.
- Seeds: Python, NumPy and torch, and torch.use_deterministic_algorithms (warn_only, so a kernel
  without a deterministic version on ROCm warns instead of failing); recorded with every result.
- Timers: Stopwatch synchronises the GPU before reading the clock, so each phase (cold start, data
  loading, training, inference) is timed apart and the GPU's queued work is not counted in the
  next phase.
"""

from __future__ import annotations

import os
import platform
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, TypeVar

import numpy as np
import torch

Backend = Literal["dgl", "torch"]
DeviceName = Literal["cpu", "gpu"]
T = TypeVar("T")


class GpuUnavailable(RuntimeError):
    """A GPU run was asked for and torch sees no GPU."""


def default_device() -> DeviceName:
    return "gpu" if os.environ.get("DEVICE", "cpu").lower() in ("gpu", "cuda") else "cpu"


def resolve_device(name: str) -> torch.device:
    if name in ("gpu", "cuda"):
        if not torch.cuda.is_available():
            raise GpuUnavailable(
                "GPU requested but torch.cuda.is_available() is False: check /dev/kfd and "
                "/dev/dri in the container, the video/render groups and torch.version.hip"
            )
        return torch.device("cuda", 0)
    if name == "cpu":
        return torch.device("cpu")
    raise ValueError(f"device must be cpu or gpu, not {name!r}")


def dgl_available() -> bool:
    try:
        os.environ.setdefault("DGLBACKEND", "pytorch")
        import dgl  # noqa: F401
    except Exception:  # an absent or broken DGL: the PyTorch layers are the same model
        return False
    return True


def resolve_backend(requested: str) -> Backend:
    if requested == "torch":
        return "torch"
    if requested == "dgl":
        if not dgl_available():
            raise RuntimeError("--backend dgl: DGL is not importable here")
        return "dgl"
    if requested == "auto":
        return "dgl" if dgl_available() else "torch"
    raise ValueError(f"backend must be auto, dgl or torch, not {requested!r}")


def seed_everything(seed: int, deterministic: bool = True) -> None:
    random.seed(seed)
    np.random.seed(seed % 2**32)
    torch.manual_seed(seed)
    if deterministic:
        # CUBLAS_WORKSPACE_CONFIG is read by cuBLAS/hipBLAS for deterministic GEMMs.
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    torch.use_deterministic_algorithms(deterministic, warn_only=True)
    if backend_dgl_loaded():
        import dgl

        dgl.seed(seed)


def backend_dgl_loaded() -> bool:
    import sys

    return "dgl" in sys.modules


def synchronize(device: torch.device) -> None:
    if device.type == "cuda":
        torch.cuda.synchronize(device)


class Stopwatch:
    """Wall-clock seconds of named phases, the GPU synchronised at both ends of each."""

    def __init__(self, device: torch.device) -> None:
        self.device = device
        self.seconds: dict[str, float] = {}

    def time(self, phase: str, fn: Callable[[], T]) -> T:
        synchronize(self.device)
        start = time.perf_counter()
        out = fn()
        synchronize(self.device)
        self.seconds[phase] = self.seconds.get(phase, 0.0) + time.perf_counter() - start
        return out

    def add(self, phase: str, seconds: float) -> None:
        self.seconds[phase] = self.seconds.get(phase, 0.0) + seconds

    def rounded(self) -> dict[str, float]:
        return {k: round(v, 4) for k, v in self.seconds.items()}


def warm_up(device: torch.device) -> float:
    """Seconds to create the device context and run the first kernels (part of the cold start)."""
    synchronize(device)
    start = time.perf_counter()
    a = torch.randn(256, 256, device=device)
    b = a @ a
    b.sum().item()
    idx = torch.arange(256, device=device)
    torch.zeros(256, 256, device=device).index_add_(0, idx, b)
    synchronize(device)
    return time.perf_counter() - start


# Rule 11: a time measured on this hardware is real (the data it ran on has its own nature).
TIMING_NATURE = "real (measured on the machine named in environment)"


def utc_now() -> str:
    """The current instant in UTC, ISO 8601 to the second with a Z: the period of a timing."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_average() -> float | None:
    try:
        return round(os.getloadavg()[0], 2)
    except (AttributeError, OSError):  # pragma: no cover - not on Linux
        return None


def environment(device: torch.device, backend: Backend) -> dict[str, Any]:
    """Versions and hardware of a run (recorded with every timing, spec 12)."""
    cpu = platform.processor() or platform.machine()
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as info:
            for line in info:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:  # pragma: no cover - not on Linux
        pass
    env: dict[str, Any] = {
        "device": str(device),
        "backend": backend,
        "python": platform.python_version(),
        "torch": torch.__version__,
        "torch_hip": getattr(torch.version, "hip", None),
        "cpu": cpu,
        "cpu_count": os.cpu_count(),
        "torch_threads": torch.get_num_threads(),
        "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
        "gpu_visible": torch.cuda.is_available(),
    }
    if backend == "dgl":
        import dgl

        env["dgl"] = dgl.__version__
    if device.type == "cuda":
        props = torch.cuda.get_device_properties(device)
        env["gpu_name"] = torch.cuda.get_device_name(device)
        env["gpu_arch"] = getattr(props, "gcnArchName", None)
        env["gpu_total_gb"] = round(props.total_memory / 1e9, 1)
    return env


def gpu_memory(device: torch.device) -> dict[str, float]:
    if device.type != "cuda":
        return {}
    free, total = torch.cuda.mem_get_info(device)
    return {
        "peak_allocated_gb": round(torch.cuda.max_memory_allocated(device) / 1e9, 3),
        "peak_reserved_gb": round(torch.cuda.max_memory_reserved(device) / 1e9, 3),
        "device_used_gb_at_end": round((total - free) / 1e9, 3),
    }

from typing import Any

from runtime.types import GpuInfo


class GpuDetectionError(RuntimeError):
    """Raised when the CUDA runtime cannot expose a usable GPU."""


class NoCudaError(GpuDetectionError):
    """Raised when CUDA is unavailable."""


def detect_gpu(torch_module: Any = None) -> GpuInfo:
    """Read the active CUDA device through a torch-like module.

    Accepting a module argument keeps this boundary testable without importing
    torch on the development machine.
    """
    if torch_module is None:
        try:
            import torch as torch_module
        except ImportError as exc:
            raise GpuDetectionError("PyTorch is not installed") from exc

    cuda = getattr(torch_module, "cuda", None)
    if cuda is None or not cuda.is_available():
        raise NoCudaError("CUDA GPU is required")

    device = cuda.current_device()
    name = str(cuda.get_device_name(device))
    capability = tuple(cuda.get_device_capability(device))
    if len(capability) != 2:
        raise GpuDetectionError(f"Invalid CUDA compute capability: {capability!r}")

    properties = cuda.get_device_properties(device)
    total_memory_mib = int(getattr(properties, "total_memory") / (1024 * 1024))
    return GpuInfo(name=name, compute_capability=(int(capability[0]), int(capability[1])), total_memory_mib=total_memory_mib)

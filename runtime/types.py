from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class GpuInfo:
    name: str
    compute_capability: Tuple[int, int]
    total_memory_mib: int

    @property
    def architecture(self) -> str:
        major, minor = self.compute_capability
        return f"sm{major}{minor}"


@dataclass(frozen=True)
class BackendChoice:
    name: str
    architecture: str
    backend_path: Optional[str] = None
    module: Optional[str] = None
    fallback_reason: Optional[str] = None


@dataclass(frozen=True)
class RuntimeProfile:
    attention: str
    spectrum_degree: int
    convrot: Optional[str]
    nvfp4: bool
    torch_compile: bool
    cpu_offload: str
    resolution_preset: str
    enabled_features: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelStatus:
    model_id: str
    path: str
    cache_hit: bool
    size_bytes: int
    verified: bool

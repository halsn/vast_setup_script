from typing import Any, Mapping

from runtime.types import BackendChoice, GpuInfo, RuntimeProfile


def build_runtime_profile(
    gpu: GpuInfo,
    choice: BackendChoice,
    matrix: Mapping[str, Any],
) -> RuntimeProfile:
    profile = dict(matrix["profiles"].get(choice.name, matrix["profiles"]["sdpa"]))

    if gpu.total_memory_mib < 16 * 1024:
        return RuntimeProfile(
            attention="sdpa",
            spectrum_degree=0,
            convrot=None,
            nvfp4=False,
            torch_compile=False,
            cpu_offload="high",
            resolution_preset="low",
            enabled_features=(),
        )

    if gpu.total_memory_mib < 24 * 1024:
        profile["torch_compile"] = False
        profile["nvfp4"] = False
        profile["spectrum_degree"] = min(int(profile.get("spectrum_degree", 0)), 1)
        profile["cpu_offload"] = "medium"
        profile["resolution_preset"] = "standard"

    return RuntimeProfile(
        attention=str(profile["attention"]),
        spectrum_degree=int(profile.get("spectrum_degree", 0)),
        convrot=profile.get("convrot"),
        nvfp4=bool(profile.get("nvfp4", False)),
        torch_compile=bool(profile.get("torch_compile", False)),
        cpu_offload=str(profile.get("cpu_offload", "high")),
        resolution_preset=str(profile.get("resolution_preset", "low")),
        enabled_features=tuple(profile.get("enabled_features", ())),
    )

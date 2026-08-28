import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from runtime.compatibility import load_compatibility, select_backend
from runtime.gpu_detector import detect_gpu
from runtime.model_cache import ModelCache, load_model_manifest
from runtime.optimizer import build_runtime_profile
from runtime.types import GpuInfo


class StartupError(RuntimeError):
    """Raised when the worker cannot prepare a ready runtime."""


def prepare_runtime(
    *,
    gpu_detector: Callable[[], GpuInfo] = lambda: detect_gpu(),
    compatibility_path: Path,
    model_manifest_path: Path,
    backend_root: Path,
    runtime_config_path: Path,
    model_cache: Optional[Any] = None,
    model_cache_root: Optional[Path] = None,
    backend_override: str = "auto",
    importer: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    try:
        gpu = gpu_detector()
        matrix = load_compatibility(compatibility_path)
        choice = select_backend(
            gpu,
            matrix,
            backend_root=backend_root,
            override=backend_override,
            importer=importer,
        )
        profile = build_runtime_profile(gpu, choice, matrix)
        specs = load_model_manifest(model_manifest_path)
        cache = model_cache or ModelCache(model_cache_root or Path("/models"))
        statuses = cache.ensure_all(specs)
    except Exception as exc:
        raise StartupError(str(exc)) from exc

    payload: Dict[str, Any] = {
        "status": "ready",
        "gpu": asdict(gpu),
        "compute_capability": gpu.architecture,
        "backend": choice.name,
        "backend_path": choice.backend_path,
        "backend_module": choice.module,
        "fallback_reason": choice.fallback_reason,
        "profile": asdict(profile),
        "models": [asdict(status) for status in statuses],
    }
    _write_json_atomic(runtime_config_path, payload)
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the H3 Worker runtime")
    parser.add_argument("--compatibility", default=os.getenv("H3_COMPATIBILITY_MATRIX", "/opt/h3/config/compatibility.json"))
    parser.add_argument("--model-manifest", default=os.getenv("H3_MODEL_MANIFEST", "/opt/h3/config/models.json"))
    parser.add_argument("--backend-root", default=os.getenv("H3_BACKEND_ROOT", "/opt/h3/backends"))
    parser.add_argument("--model-cache", default=os.getenv("MODEL_CACHE_DIR", "/models"))
    parser.add_argument("--runtime-config", default=os.getenv("H3_RUNTIME_CONFIG", "/run/h3/runtime.json"))
    parser.add_argument("--backend-override", default=os.getenv("H3_BACKEND_OVERRIDE", "auto"))
    args = parser.parse_args(argv)

    try:
        payload = prepare_runtime(
            compatibility_path=Path(args.compatibility),
            model_manifest_path=Path(args.model_manifest),
            backend_root=Path(args.backend_root),
            runtime_config_path=Path(args.runtime_config),
            model_cache_root=Path(args.model_cache),
            backend_override=args.backend_override,
        )
    except StartupError as exc:
        print(
            json.dumps(
                {"event": "startup_failed", "error_type": type(exc).__name__, "error": str(exc)},
                sort_keys=True,
            )
        )
        return 1

    print(json.dumps({"event": "runtime_ready", **payload}, sort_keys=True))
    return 0


def _write_json_atomic(path: Path, payload: Dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


if __name__ == "__main__":
    sys.exit(main())

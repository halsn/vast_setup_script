import importlib
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from runtime.types import BackendChoice, GpuInfo


class CompatibilityError(ValueError):
    """Raised when the compatibility matrix is malformed."""


class BackendSelectionError(RuntimeError):
    """Raised when an explicitly requested backend cannot be used."""


def load_compatibility(path: Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        matrix = json.load(stream)

    if not isinstance(matrix, dict) or not isinstance(matrix.get("architectures"), dict):
        raise CompatibilityError("compatibility matrix must contain an architectures object")
    if not isinstance(matrix.get("profiles"), dict) or "sdpa" not in matrix["profiles"]:
        raise CompatibilityError("compatibility matrix must define a sdpa profile")
    return matrix


def select_backend(
    gpu: GpuInfo,
    matrix: Mapping[str, Any],
    *,
    backend_root: Path = Path("/opt/h3/backends"),
    override: str = "auto",
    importer: Optional[Callable[[str], Any]] = None,
) -> BackendChoice:
    importer = importer or importlib.import_module
    architectures = matrix["architectures"]
    entries = list(architectures.values())

    if override == "sdpa":
        return _sdpa_choice("explicit SDPA override")

    if override != "auto":
        entry = next((item for item in entries if item.get("backend") == override), None)
        if entry is None:
            raise BackendSelectionError(f"unknown backend override: {override}")
        if entry.get("architecture") != gpu.architecture:
            raise BackendSelectionError(
                f"backend {override} targets {entry.get('architecture')}, not {gpu.architecture}"
            )
        return _load_backend(entry, backend_root, importer, explicit=True)

    entry = architectures.get(gpu.architecture)
    if entry is None:
        return _sdpa_choice(f"unsupported compute capability {gpu.architecture}")
    return _load_backend(entry, backend_root, importer, explicit=False)


def _load_backend(
    entry: Mapping[str, Any],
    backend_root: Path,
    importer: Callable[[str], Any],
    *,
    explicit: bool,
) -> BackendChoice:
    backend = str(entry["backend"])
    architecture = str(entry["architecture"])
    path = Path(backend_root) / str(entry["path"])
    if not path.exists():
        reason = f"backend {backend} not installed at {path}"
        if explicit:
            raise BackendSelectionError(reason)
        return _sdpa_choice(reason)

    module = entry.get("module")
    if module:
        try:
            sys.path.insert(0, str(path))
            try:
                importer(str(module))
            finally:
                sys.path.remove(str(path))
        except Exception as exc:
            reason = f"backend {backend} import failed: {exc}"
            if explicit:
                raise BackendSelectionError(reason) from exc
            return _sdpa_choice(reason)

    return BackendChoice(
        name=backend,
        architecture=architecture,
        backend_path=str(path),
        module=str(module) if module else None,
    )


def _sdpa_choice(reason: str) -> BackendChoice:
    return BackendChoice(name="sdpa", architecture="generic", fallback_reason=reason)

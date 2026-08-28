import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

from runtime.types import ModelStatus


class ModelCacheError(RuntimeError):
    """Raised when a model cannot be downloaded and verified."""


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    filename: str
    urls: Tuple[str, ...]
    size_bytes: int
    sha256: str
    subdir: str = ""


def load_model_manifest(path: Path) -> List[ModelSpec]:
    with Path(path).open("r", encoding="utf-8") as stream:
        document = json.load(stream)

    if document.get("schema_version") != 1:
        raise ModelCacheError("model manifest schema_version must be 1")

    models = document.get("models")
    if not isinstance(models, list):
        raise ModelCacheError("model manifest models must be an array")

    result = []
    for raw in models:
        if not isinstance(raw, dict):
            raise ModelCacheError("each model entry must be an object")
        try:
            spec = ModelSpec(
                model_id=str(raw["id"]),
                filename=str(raw["filename"]),
                urls=tuple(str(url) for url in raw["urls"]),
                size_bytes=int(raw["size_bytes"]),
                sha256=str(raw["sha256"]).lower(),
                subdir=str(raw.get("subdir", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ModelCacheError(f"invalid model entry: {raw!r}") from exc
        _validate_spec(spec)
        result.append(spec)
    return result


class ModelCache:
    def __init__(
        self,
        root: Path,
        *,
        opener: Optional[Callable] = None,
        timeout: float = 60.0,
        lock_timeout: float = 300.0,
    ):
        self.root = Path(root)
        self.opener = opener or urllib.request.urlopen
        self.timeout = timeout
        self.lock_timeout = lock_timeout

    def ensure(self, spec: ModelSpec) -> ModelStatus:
        _validate_spec(spec)
        target = self.root / spec.subdir / spec.filename
        partial = target.with_suffix(target.suffix + ".partial")
        lock_path = target.with_suffix(target.suffix + ".lock")
        target.parent.mkdir(parents=True, exist_ok=True)

        if _verified_file(target, spec):
            return _status(spec, target, cache_hit=True)

        with _file_lock(lock_path, self.lock_timeout):
            if _verified_file(target, spec):
                return _status(spec, target, cache_hit=True)

            errors = []
            for index, url in enumerate(spec.urls):
                try:
                    self._download(url, partial)
                    if not _verified_file(partial, spec):
                        raise ModelCacheError(
                            f"verification failed for {spec.model_id}: expected {spec.size_bytes} bytes and sha256 {spec.sha256}"
                        )
                    os.replace(partial, target)
                    return _status(spec, target, cache_hit=False)
                except (ModelCacheError, OSError, urllib.error.URLError) as exc:
                    errors.append(f"{url}: {exc}")
                    if index < len(spec.urls) - 1:
                        partial.unlink(missing_ok=True)

            raise ModelCacheError(
                f"could not prepare model {spec.model_id}; partial file retained at {partial}: "
                + "; ".join(errors)
            )

    def ensure_all(self, specs: Iterable[ModelSpec]) -> List[ModelStatus]:
        return [self.ensure(spec) for spec in specs]

    def _download(self, url: str, partial: Path) -> None:
        existing_size = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={existing_size}-"} if existing_size else {}
        request = urllib.request.Request(url, headers=headers)

        with self.opener(request, timeout=self.timeout) as response:
            status = getattr(response, "status", 200)
            append = existing_size > 0 and status == 206
            mode = "ab" if append else "wb"
            with partial.open(mode) as output:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)


def _validate_spec(spec: ModelSpec) -> None:
    if not spec.model_id or not spec.filename or not spec.urls:
        raise ModelCacheError("model id, filename and at least one URL are required")
    if Path(spec.filename).name != spec.filename or Path(spec.filename).is_absolute():
        raise ModelCacheError(f"unsafe model filename: {spec.filename}")
    if any(part in ("", ".", "..") for part in Path(spec.subdir).parts if part != "."):
        raise ModelCacheError(f"unsafe model subdir: {spec.subdir}")
    if spec.size_bytes < 0 or len(spec.sha256) != 64 or any(char not in "0123456789abcdef" for char in spec.sha256):
        raise ModelCacheError(f"invalid size or sha256 for model {spec.model_id}")


def _verified_file(path: Path, spec: ModelSpec) -> bool:
    if not path.is_file() or path.stat().st_size != spec.size_bytes:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == spec.sha256


def _status(spec: ModelSpec, target: Path, *, cache_hit: bool) -> ModelStatus:
    return ModelStatus(
        model_id=spec.model_id,
        path=str(target),
        cache_hit=cache_hit,
        size_bytes=target.stat().st_size,
        verified=True,
    )


@contextmanager
def _file_lock(path: Path, timeout: float):
    deadline = time.monotonic() + timeout
    while True:
        try:
            descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(descriptor)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise ModelCacheError(f"timed out waiting for model lock {path}")
            time.sleep(0.05)

    try:
        yield
    finally:
        path.unlink(missing_ok=True)

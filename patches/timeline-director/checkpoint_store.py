"""Safe filesystem checkpoints for the pinned Timeline Director SelfLift patch."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Callable
from uuid import UUID


CHECKPOINT_SCHEMA_VERSION = 1
CHECKPOINT_SCHEMA = "minimax-h3-timeline-director-checkpoint"
STATE_FILENAME = "segment-0001.pt"
MANIFEST_FILENAME = "manifest.json"
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")


class CheckpointError(ValueError):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


def segment_stages(execution_stage: str, segment_count: int) -> tuple[str, ...]:
    if execution_stage not in {"full", "preview", "finalize"}:
        raise CheckpointError("execution_stage must be full, preview, or finalize")
    if isinstance(segment_count, bool) or not isinstance(segment_count, int) or segment_count < 1:
        raise CheckpointError("segment_count must be a positive integer")
    if execution_stage == "preview":
        return ("preview",) * segment_count
    if execution_stage == "finalize":
        return ("finalize",) + ("full",) * (segment_count - 1)
    return ("full",) * segment_count


def _checkpoint_id(value: str) -> str:
    if not isinstance(value, str):
        raise CheckpointError("Invalid checkpoint ID")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise CheckpointError("Invalid checkpoint ID") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise CheckpointError("Invalid checkpoint ID")
    return value


def _root(output_directory: str | Path) -> Path:
    return Path(output_directory).resolve() / "timeline_director_checkpoints"


def _directory(output_directory: str | Path, checkpoint_id: str) -> Path:
    root = _root(output_directory)
    directory = root / _checkpoint_id(checkpoint_id)
    resolved_root = root.resolve()
    resolved = directory.resolve()
    if resolved.parent != resolved_root:
        raise CheckpointError("Checkpoint path escapes its managed directory")
    return directory


def _atomic_write(destination: Path, contents: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def stage_first_segment(
    output_directory: str | Path, checkpoint_id: str, tensor_payload: bytes
) -> Path:
    if not isinstance(tensor_payload, bytes) or not tensor_payload:
        raise CheckpointError("First-segment tensor checkpoint is empty")
    directory = _directory(output_directory, checkpoint_id)
    directory.mkdir(parents=True, exist_ok=True)
    state_path = directory / STATE_FILENAME
    manifest_path = directory / MANIFEST_FILENAME
    if state_path.exists() or manifest_path.exists():
        raise CheckpointError("Checkpoint ID has already been used", 409)
    _atomic_write(state_path, tensor_payload)
    return state_path


def commit_preview(
    output_directory: str | Path,
    checkpoint_id: str,
    *,
    fingerprint: str,
    segment_count: int,
    metadata: dict,
) -> None:
    if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
        raise CheckpointError("Invalid sampling fingerprint")
    if isinstance(segment_count, bool) or not isinstance(segment_count, int) or segment_count < 1:
        raise CheckpointError("segment_count must be a positive integer")
    if not isinstance(metadata, dict):
        raise CheckpointError("Checkpoint metadata must be an object")

    directory = _directory(output_directory, checkpoint_id)
    state_path = directory / STATE_FILENAME
    if not state_path.is_file() or state_path.is_symlink() or state_path.stat().st_size < 1:
        raise CheckpointError("First-segment resume state is incomplete", 409)
    if (directory / MANIFEST_FILENAME).exists():
        raise CheckpointError("Checkpoint manifest already exists", 409)

    state_digest = hashlib.sha256(state_path.read_bytes()).hexdigest()
    manifest = {
        "schema": CHECKPOINT_SCHEMA,
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": _checkpoint_id(checkpoint_id),
        "fingerprint": fingerprint,
        "segment_count": segment_count,
        "resume_segment": 1,
        "state_file": STATE_FILENAME,
        "state_size": state_path.stat().st_size,
        "state_sha256": state_digest,
        "metadata": metadata,
    }
    try:
        data = json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CheckpointError("Checkpoint metadata is not JSON serializable") from exc
    _atomic_write(directory / MANIFEST_FILENAME, data)


def _read_validated_manifest(
    output_directory: str | Path,
    checkpoint_id: str,
    *,
    fingerprint: str,
    segment_count: int,
) -> tuple[dict, Path]:
    directory = _directory(output_directory, checkpoint_id)
    manifest_path = directory / MANIFEST_FILENAME
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise CheckpointError("Checkpoint manifest is missing", 404)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CheckpointError("Checkpoint manifest is unreadable", 409) from exc

    if not isinstance(manifest, dict):
        raise CheckpointError("Checkpoint manifest is malformed", 409)
    if manifest.get("schema") != CHECKPOINT_SCHEMA:
        raise CheckpointError("Unsupported checkpoint schema", 409)
    if manifest.get("schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise CheckpointError("Unsupported checkpoint schema version", 409)
    if manifest.get("checkpoint_id") != _checkpoint_id(checkpoint_id):
        raise CheckpointError("Checkpoint ID does not match manifest", 409)
    if manifest.get("fingerprint") != fingerprint:
        raise CheckpointError("Sampling fingerprint does not match checkpoint", 409)
    if manifest.get("segment_count") != segment_count:
        raise CheckpointError("Timeline segment count does not match checkpoint", 409)
    if manifest.get("resume_segment") != 1:
        raise CheckpointError("Checkpoint does not contain the first segment", 409)
    if manifest.get("state_file") != STATE_FILENAME:
        raise CheckpointError("Checkpoint state path is invalid", 409)

    state_path = directory / STATE_FILENAME
    if not state_path.is_file() or state_path.is_symlink():
        raise CheckpointError("Checkpoint tensor state is missing", 409)
    state_size = state_path.stat().st_size
    if state_size < 1 or manifest.get("state_size") != state_size:
        raise CheckpointError("Checkpoint tensor state size is invalid", 409)
    digest = hashlib.sha256(state_path.read_bytes()).hexdigest()
    if manifest.get("state_sha256") != digest:
        raise CheckpointError("Checkpoint tensor state checksum is invalid", 409)
    if not isinstance(manifest.get("metadata"), dict):
        raise CheckpointError("Checkpoint metadata is malformed", 409)
    return manifest, state_path


def checkpoint_status(
    output_directory: str | Path,
    checkpoint_id: str,
    *,
    fingerprint: str,
    segment_count: int,
) -> dict:
    manifest, _ = _read_validated_manifest(
        output_directory,
        checkpoint_id,
        fingerprint=fingerprint,
        segment_count=segment_count,
    )
    return {
        "status": "ready",
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": manifest["checkpoint_id"],
        "fingerprint": manifest["fingerprint"],
        "segment_count": manifest["segment_count"],
        "resume_segment": manifest["resume_segment"],
    }


def load_first_segment(
    output_directory: str | Path,
    checkpoint_id: str,
    *,
    fingerprint: str,
    segment_count: int,
    loader: Callable[[Path], object] | None = None,
) -> tuple[dict, object]:
    manifest, state_path = _read_validated_manifest(
        output_directory,
        checkpoint_id,
        fingerprint=fingerprint,
        segment_count=segment_count,
    )
    if loader is None:
        import torch

        loader = lambda path: torch.load(path, map_location="cpu", weights_only=True)
    return manifest, loader(state_path)


def discard_checkpoint(output_directory: str | Path, checkpoint_id: str) -> bool:
    directory = _directory(output_directory, checkpoint_id)
    if not directory.exists():
        return False
    if directory.is_symlink() or not directory.is_dir():
        raise CheckpointError("Managed checkpoint path is not a directory", 409)
    shutil.rmtree(directory)
    return True


def register_checkpoint_routes(route_table, output_directory: Callable[[], str | Path]) -> None:
    async def get_checkpoint(request):
        from aiohttp import web

        try:
            segment_count = int(request.query.get("segment_count", ""))
            result = checkpoint_status(
                output_directory(),
                request.match_info.get("checkpoint_id", ""),
                fingerprint=request.query.get("fingerprint", ""),
                segment_count=segment_count,
            )
            return web.json_response(result)
        except (ValueError, CheckpointError) as exc:
            status_code = exc.status_code if isinstance(exc, CheckpointError) else 400
            return web.json_response(
                {"status": "invalid", "error": str(exc)}, status=status_code
            )

    async def delete_checkpoint_route(request):
        from aiohttp import web

        try:
            deleted = discard_checkpoint(
                output_directory(), request.match_info.get("checkpoint_id", "")
            )
            if not deleted:
                raise CheckpointError("Checkpoint does not exist", 404)
            return web.json_response({"status": "deleted"})
        except CheckpointError as exc:
            return web.json_response(
                {"status": "invalid", "error": str(exc)}, status=exc.status_code
            )

    route_table.get("/minimax_h3_timeline/checkpoints/{checkpoint_id}")(get_checkpoint)
    route_table.delete("/minimax_h3_timeline/checkpoints/{checkpoint_id}")(
        delete_checkpoint_route
    )

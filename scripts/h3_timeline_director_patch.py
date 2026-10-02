#!/usr/bin/env python3
"""Safely apply the setup-managed Timeline Director patch to its pinned checkout."""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


LEGACY_HELPER_RELATIVE_PATH = "selflift_runtime/checkpoint_store.py"
HELPER_RELATIVE_PATHS = {
    "selflift_runtime/checkpoint_store.py",
    "selflift_runtime/reference_cache.py",
}
TRACKED_PATCH_PATHS = {
    "__init__.py",
    "minimax_h3_finite_segments.py",
    "minimax_h3_timeline_director.py",
    "selflift_runtime/nodes.py",
}
# Exact previously shipped tracked diffs; never accept local edits.
PREVIOUS_PATCH_SHA256 = {
    "5044f3e62978862989d5c7214b48585cdbbed89b2c4cd9c5ba37b20685e9ef38",
    "61c1578c05181ec2aca4baadfdaedfe35315503f9fafc3f32de1986afd684ea9",
    "53c42f1ae25500e9c6d5f9c424653ad840e92e56595e1b8dd937fee83e358b7e",
    "0565ed92bd940dcb972f2b3d78c61cf994f68612c8a263a3291ff0ab2373f262",
    "5c9805aae8cd0a755296084be456b0807e49d685d1178e8284d768a028ac1b8b",
}
PREVIOUS_HELPER_SHA256 = {
    "978f882e7c4e587c48719bc3c4a425e3ff03a7468923316e4a39dd0a97b674c4",
    "8fa4b51eee8183880ca3a59aafa536662f080236d54c81581209610f2b2b6475",
}
EXPECTED_PATCH_PATHS = TRACKED_PATCH_PATHS | HELPER_RELATIVE_PATHS


class PatchError(RuntimeError):
    pass


def _git(repo: Path, *args: str, input_data: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=repo, input=input_data, capture_output=True, check=False
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise PatchError(detail or f"git {' '.join(args)} failed")
    return result.stdout


def _normalized_remote(value: str) -> tuple[str, str]:
    value = value.strip()
    if value.startswith("git@") and ":" in value:
        host, path = value[4:].split(":", 1)
    else:
        parsed = urlparse(value)
        host, path = parsed.hostname or "", parsed.path.lstrip("/")
    path = path.removesuffix(".git").rstrip("/")
    return host.lower(), path.lower()


def _status(repo: Path) -> list[tuple[str, str]]:
    output = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries = []
    for item in output.decode("utf-8", "surrogateescape").split("\0"):
        if not item:
            continue
        # Setup creates this untracked workflow alias; patch operations never touch it.
        if item == "?? example_workflows/h3_timeline_director.json":
            alias = repo / "example_workflows/h3_timeline_director.json"
            if alias.is_file() and not alias.is_symlink():
                continue
        entries.append((item[:2], item[3:]))
    return entries


def _patch_sections(patch_path: Path) -> dict[str, str]:
    text = patch_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    chunks = re.split(r"(?=^diff --git )", text, flags=re.MULTILINE)
    sections = {}
    for chunk in chunks:
        if not chunk.startswith("diff --git "):
            if chunk.strip():
                raise PatchError("Patch contains data outside a Git diff section")
            continue
        match = re.match(r"diff --git a/(.+?) b/(.+?)\n", chunk)
        if not match or match.group(1) != match.group(2):
            raise PatchError("Patch contains an unsupported path mapping")
        path = match.group(1)
        if path in sections:
            raise PatchError(f"Patch repeats a file section: {path}")
        sections[path] = chunk.rstrip("\n") + "\n"
    if set(sections) != EXPECTED_PATCH_PATHS:
        raise PatchError(
            "Patch file set does not match the managed Timeline Director contract: "
            + ", ".join(sorted(sections))
        )
    return sections


def _patch_parts(patch_path: Path) -> tuple[bytes, dict[str, bytes]]:
    sections = _patch_sections(patch_path)
    tracked = "".join(sections[path] for path in sorted(TRACKED_PATCH_PATHS))
    helpers = {
        path: sections[path].encode("utf-8")
        for path in sorted(HELPER_RELATIVE_PATHS)
    }
    return tracked.encode("utf-8"), helpers


def _embedded_helper_sources(patch_path: Path) -> dict[str, bytes]:
    sections = _patch_sections(patch_path)
    embedded = {}
    for path in HELPER_RELATIVE_PATHS:
        section = sections[path]
        if "new file mode " not in section or "\n--- /dev/null\n" not in section:
            raise PatchError(f"Managed helper is not a new file in the patch: {path}")
        lines = section.splitlines(keepends=True)
        in_hunk = False
        content = []
        for line in lines:
            if line.startswith("@@ "):
                in_hunk = True
                continue
            if not in_hunk or line.startswith("\\ No newline at end of file"):
                continue
            if line.startswith("+") and not line.startswith("+++"):
                content.append(line[1:])
            elif line.startswith(("-", " ")):
                raise PatchError(f"Managed helper patch has unexpected context: {path}")
        embedded[path] = "".join(content).encode("utf-8")
    return embedded


def _validate_helper_sources(patch_path: Path, helpers: dict[str, Path]) -> None:
    embedded = _embedded_helper_sources(patch_path)
    for path, expected in embedded.items():
        source = helpers[path]
        if not source.is_file() or source.is_symlink():
            raise PatchError(f"Managed helper source is missing: {source}")
        actual = source.read_bytes().replace(b"\r\n", b"\n")
        if actual != expected.replace(b"\r\n", b"\n"):
            raise PatchError(f"Managed helper source does not match patch: {path}")


def _managed_applied(
    repo: Path, patch_path: Path, helpers: dict[str, Path], tracked_patch: bytes | None = None
) -> bool:
    if tracked_patch is None:
        tracked_patch, _ = _patch_parts(patch_path)
    actual_diff = _git(
        repo, "-c", "core.autocrlf=true", "diff", "--no-ext-diff", "--binary"
    ).replace(b"\r\n", b"\n")
    expected_status = {
        (" M", "__init__.py"),
        (" M", "minimax_h3_finite_segments.py"),
        (" M", "minimax_h3_timeline_director.py"),
        (" M", "selflift_runtime/nodes.py"),
        *(("??", path) for path in HELPER_RELATIVE_PATHS),
    }
    if set(_status(repo)) != expected_status or actual_diff != tracked_patch:
        return False
    for relative_path in HELPER_RELATIVE_PATHS:
        managed = repo / relative_path
        source = helpers[relative_path]
        if (
            not managed.is_file()
            or managed.is_symlink()
            or not source.is_file()
            or managed.read_bytes().replace(b"\r\n", b"\n")
            != source.read_bytes().replace(b"\r\n", b"\n")
        ):
            return False
    return True


def _relative_helpers(helpers: dict[str, Path]) -> dict[str, Path]:
    normalized = {
        key if key.startswith("selflift_runtime/") else f"selflift_runtime/{key}": path
        for key, path in helpers.items()
    }
    if set(normalized) != HELPER_RELATIVE_PATHS:
        raise PatchError("Expected both managed Timeline Director helper sources")
    return normalized


def _previous_managed_applied(repo: Path) -> bytes | None:
    previous = _git(
        repo, "-c", "core.autocrlf=true", "diff", "--no-ext-diff", "--binary"
    ).replace(b"\r\n", b"\n")
    expected_status = {
        (" M", "__init__.py"),
        (" M", "minimax_h3_finite_segments.py"),
        (" M", "selflift_runtime/nodes.py"),
        ("??", LEGACY_HELPER_RELATIVE_PATH),
    }
    helper = repo / LEGACY_HELPER_RELATIVE_PATH
    if (
        set(_status(repo)) != expected_status
        or hashlib.sha256(previous).hexdigest() not in PREVIOUS_PATCH_SHA256
        or not helper.is_file()
        or helper.is_symlink()
        or hashlib.sha256(helper.read_bytes()).hexdigest() not in PREVIOUS_HELPER_SHA256
    ):
        return None
    return previous


def _validate_repo(repo: Path, expected_remote: str) -> None:
    if not repo.is_dir():
        raise PatchError(f"Timeline Director checkout does not exist: {repo}")
    if _git(repo, "rev-parse", "--is-inside-work-tree").strip() != b"true":
        raise PatchError(f"Timeline Director path is not a Git checkout: {repo}")
    try:
        actual_remote = _git(repo, "remote", "get-url", "origin").decode().strip()
    except PatchError as exc:
        raise PatchError("Timeline Director checkout has no origin remote") from exc
    if _normalized_remote(actual_remote) != _normalized_remote(expected_remote):
        raise PatchError(f"Unexpected Timeline Director origin: {actual_remote}")


def prepare(repo: Path, patch_path: Path, helpers: dict[str, Path], revision: str, remote: str) -> None:
    helpers = _relative_helpers(helpers)
    _validate_helper_sources(patch_path, helpers)
    _validate_repo(repo, remote)
    status = _status(repo)
    if not status:
        return
    current = _git(repo, "rev-parse", "HEAD").decode().strip()
    tracked_patch, helper_patches = _patch_parts(patch_path)
    if _managed_applied(repo, patch_path, helpers):
        reverse_helpers = b"".join(helper_patches.values())
        remove_legacy_helper = False
    else:
        previous = _previous_managed_applied(repo)
        if previous is None:
            raise PatchError(
                "Refusing to reset Timeline Director: local changes are not exactly the managed patch"
            )
        tracked_patch = previous
        reverse_helpers = b""
        remove_legacy_helper = True
    if current != revision:
        raise PatchError(
            "Refusing to reset Timeline Director: local changes are not exactly the managed patch"
        )
    reversible_patch = tracked_patch + reverse_helpers
    _git(repo, "apply", "--reverse", "--check", "-", input_data=reversible_patch)
    _git(repo, "apply", "--reverse", "-", input_data=reversible_patch)
    if remove_legacy_helper:
        (repo / LEGACY_HELPER_RELATIVE_PATH).unlink()
    if _status(repo) or any((repo / path).exists() for path in HELPER_RELATIVE_PATHS | {LEGACY_HELPER_RELATIVE_PATH}):
        raise PatchError("Managed Timeline Director patch did not reverse to a clean checkout")


def apply(repo: Path, patch_path: Path, helpers: dict[str, Path], revision: str, remote: str) -> None:
    helpers = _relative_helpers(helpers)
    _validate_helper_sources(patch_path, helpers)
    _validate_repo(repo, remote)
    current = _git(repo, "rev-parse", "HEAD").decode().strip()
    if current != revision:
        raise PatchError(f"Timeline Director base revision mismatch: {current} != {revision}")
    status = _status(repo)
    if status:
        if _managed_applied(repo, patch_path, helpers):
            return
        raise PatchError("Refusing to apply patch over unrelated Timeline Director changes")
    _patch_parts(patch_path)
    _git(repo, "apply", "--check", str(patch_path))
    _git(repo, "apply", str(patch_path))
    if not _managed_applied(repo, patch_path, helpers):
        raise PatchError("Applied Timeline Director patch does not match its managed source")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "apply"))
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--helper", required=True, action="append", type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--remote", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise PatchError("Expected Timeline Director revision must be a full commit SHA")
    helper_names = {path.name for path in args.helper}
    helper_paths = {f"selflift_runtime/{path.name}": path for path in args.helper}
    if (
        not args.patch.is_file()
        or len(helper_paths) != len(args.helper)
        or helper_names != {Path(path).name for path in HELPER_RELATIVE_PATHS}
        or any(not path.is_file() for path in helper_paths.values())
    ):
        raise PatchError("Managed Timeline Director patch or helper source is missing")
    operation = prepare if args.mode == "prepare" else apply
    operation(args.repo, args.patch, helper_paths, args.revision, args.remote)
    print(f"[OK] Timeline Director patch {args.mode} complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PatchError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)

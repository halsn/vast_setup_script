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


HELPER_RELATIVE_PATH = "selflift_runtime/checkpoint_store.py"
# Exact previously shipped tracked diffs; never accept local edits.
PREVIOUS_PATCH_SHA256 = {
    "5044f3e62978862989d5c7214b48585cdbbed89b2c4cd9c5ba37b20685e9ef38",
    "61c1578c05181ec2aca4baadfdaedfe35315503f9fafc3f32de1986afd684ea9",
    "53c42f1ae25500e9c6d5f9c424653ad840e92e56595e1b8dd937fee83e358b7e",
}
EXPECTED_PATCH_PATHS = {
    "__init__.py",
    "minimax_h3_finite_segments.py",
    "selflift_runtime/nodes.py",
    HELPER_RELATIVE_PATH,
}


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


def _patch_parts(patch_path: Path) -> tuple[bytes, bytes]:
    sections = _patch_sections(patch_path)
    tracked = "".join(
        sections[path]
        for path in sorted(EXPECTED_PATCH_PATHS - {HELPER_RELATIVE_PATH})
    )
    return tracked.encode("utf-8"), sections[HELPER_RELATIVE_PATH].encode("utf-8")


def _managed_applied(
    repo: Path, patch_path: Path, helper_path: Path, tracked_patch: bytes | None = None
) -> bool:
    if tracked_patch is None:
        tracked_patch, _ = _patch_parts(patch_path)
    actual_diff = _git(
        repo, "-c", "core.autocrlf=true", "diff", "--no-ext-diff", "--binary"
    ).replace(b"\r\n", b"\n")
    helper = repo / HELPER_RELATIVE_PATH
    expected_status = {
        (" M", "__init__.py"),
        (" M", "minimax_h3_finite_segments.py"),
        (" M", "selflift_runtime/nodes.py"),
        ("??", HELPER_RELATIVE_PATH),
    }
    return (
        set(_status(repo)) == expected_status
        and actual_diff == tracked_patch
        and helper.is_file()
        and not helper.is_symlink()
        and helper.read_bytes().replace(b"\r\n", b"\n")
        == helper_path.read_bytes().replace(b"\r\n", b"\n")
    )


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


def prepare(repo: Path, patch_path: Path, helper_path: Path, revision: str, remote: str) -> None:
    _validate_repo(repo, remote)
    status = _status(repo)
    if not status:
        return
    current = _git(repo, "rev-parse", "HEAD").decode().strip()
    tracked_patch, helper_patch = _patch_parts(patch_path)
    if not _managed_applied(repo, patch_path, helper_path):
        previous = _git(repo, "-c", "core.autocrlf=true", "diff", "--no-ext-diff", "--binary").replace(b"\r\n", b"\n")
        if (
            hashlib.sha256(previous).hexdigest() in PREVIOUS_PATCH_SHA256
            and _managed_applied(repo, patch_path, helper_path, previous)
        ):
            tracked_patch = previous
        else:
            raise PatchError(
                "Refusing to reset Timeline Director: local changes are not exactly the managed patch"
            )
    if current != revision:
        raise PatchError(
            "Refusing to reset Timeline Director: local changes are not exactly the managed patch"
        )
    reversible_patch = tracked_patch + helper_patch
    _git(repo, "apply", "--reverse", "--check", "-", input_data=reversible_patch)
    _git(repo, "apply", "--reverse", "-", input_data=reversible_patch)
    if _status(repo) or (repo / HELPER_RELATIVE_PATH).exists():
        raise PatchError("Managed Timeline Director patch did not reverse to a clean checkout")


def apply(repo: Path, patch_path: Path, helper_path: Path, revision: str, remote: str) -> None:
    _validate_repo(repo, remote)
    current = _git(repo, "rev-parse", "HEAD").decode().strip()
    if current != revision:
        raise PatchError(f"Timeline Director base revision mismatch: {current} != {revision}")
    status = _status(repo)
    if status:
        if _managed_applied(repo, patch_path, helper_path):
            return
        raise PatchError("Refusing to apply patch over unrelated Timeline Director changes")
    _patch_parts(patch_path)
    _git(repo, "apply", "--check", str(patch_path))
    _git(repo, "apply", str(patch_path))
    if not _managed_applied(repo, patch_path, helper_path):
        raise PatchError("Applied Timeline Director patch does not match its managed source")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "apply"))
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--patch", required=True, type=Path)
    parser.add_argument("--helper", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--remote", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        raise PatchError("Expected Timeline Director revision must be a full commit SHA")
    if not args.patch.is_file() or not args.helper.is_file():
        raise PatchError("Managed Timeline Director patch or helper source is missing")
    operation = prepare if args.mode == "prepare" else apply
    operation(args.repo, args.patch, args.helper, args.revision, args.remote)
    print(f"[OK] Timeline Director patch {args.mode} complete")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PatchError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)

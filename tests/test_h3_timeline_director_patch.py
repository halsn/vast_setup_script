from __future__ import annotations

from pathlib import Path
import hashlib
import importlib.util
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCH_TOOL = ROOT / "scripts" / "h3_timeline_director_patch.py"
REMOTE = "https://github.com/Songssx/ComfyUI-MiniMaxH3-TimelineDirector.git"
LEGACY_HELPER = "selflift_runtime/checkpoint_store.py"
HELPERS = ("checkpoint_store.py", "reference_cache.py")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _make_fixture(tmp_path: Path, *, legacy: bool = False):
    repo = tmp_path / "plugin"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    tracked_paths = (
        "__init__.py",
        "minimax_h3_finite_segments.py",
        "selflift_runtime/nodes.py",
    ) + (() if legacy else ("minimax_h3_timeline_director.py",))
    for path in tracked_paths:
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", *tracked_paths)
    _git(repo, "commit", "-qm", "pinned base")
    revision = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", REMOTE)

    helper_names = ("checkpoint_store.py",) if legacy else HELPERS
    helpers = {}
    for name in helper_names:
        helper = tmp_path / name
        helper.write_text(f"MANAGED_{name.upper().replace('.', '_')} = True\n", encoding="utf-8")
        helpers[name] = helper
    for path in tracked_paths:
        (repo / path).write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "selflift_runtime").mkdir(exist_ok=True)
    for name, helper in helpers.items():
        managed_helper = repo / "selflift_runtime" / name
        managed_helper.write_bytes(helper.read_bytes())
        _git(repo, "add", "-N", f"selflift_runtime/{name}")
    patch = tmp_path / "managed.patch"
    diff = subprocess.run(
        ["git", "-c", "core.autocrlf=true", "diff", "--binary"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    patch.write_text(diff, encoding="utf-8")
    _git(repo, "reset", "-q")
    return repo, patch, helpers, revision


def _run(mode: str, repo: Path, patch: Path, helpers: dict[str, Path], revision: str):
    command = [
            sys.executable,
            str(PATCH_TOOL),
            mode,
            "--repo",
            str(repo),
            "--patch",
            str(patch),
            "--revision",
            revision,
            "--remote",
            REMOTE,
        ]
    for helper in helpers.values():
        command.extend(("--helper", str(helper)))
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
    )


def test_timeline_patch_install_and_rerun_are_idempotent(tmp_path):
    repo, patch, helpers, revision = _make_fixture(tmp_path)

    prepared = _run("prepare", repo, patch, helpers, revision)
    assert prepared.returncode == 0, prepared.stderr
    applied = _run("apply", repo, patch, helpers, revision)
    assert applied.returncode == 0, applied.stderr
    assert (repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    for name, helper in helpers.items():
        assert (repo / f"selflift_runtime/{name}").read_bytes() == helper.read_bytes()

    rerun = _run("apply", repo, patch, helpers, revision)
    assert rerun.returncode == 0, rerun.stderr
    reverse = _run("prepare", repo, patch, helpers, revision)
    assert reverse.returncode == 0, reverse.stderr
    assert (repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    for name in helpers:
        assert not (repo / f"selflift_runtime/{name}").exists()
    assert _run("prepare", repo, patch, helpers, revision).returncode == 0


def test_timeline_patch_refuses_unrelated_dirty_state_without_removing_it(tmp_path):
    repo, patch, helpers, revision = _make_fixture(tmp_path)
    assert _run("apply", repo, patch, helpers, revision).returncode == 0
    user_file = repo / "user-change.txt"
    user_file.write_text("keep me\n", encoding="utf-8")

    result = _run("prepare", repo, patch, helpers, revision)

    assert result.returncode != 0
    assert user_file.read_text(encoding="utf-8") == "keep me\n"
    assert (repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_timeline_patch_refuses_helper_source_drift_before_modifying_checkout(tmp_path):
    repo, patch, helpers, revision = _make_fixture(tmp_path)
    for relative in (
        "__init__.py",
        "minimax_h3_finite_segments.py",
        "minimax_h3_timeline_director.py",
        "selflift_runtime/nodes.py",
    ):
        (repo / relative).write_text("VALUE = 1\n", encoding="utf-8")
    for name in HELPERS:
        (repo / "selflift_runtime" / name).unlink()
    helpers["reference_cache.py"].write_text("LOCAL_HELPER = True\n", encoding="utf-8")

    result = _run("apply", repo, patch, helpers, revision)

    assert result.returncode != 0
    assert "does not match patch" in result.stderr
    assert _git(repo, "status", "--porcelain") == ""
    assert (repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    for name in HELPERS:
        assert not (repo / f"selflift_runtime/{name}").exists()


def test_timeline_prepare_refuses_helper_source_drift_before_reversing_checkout(tmp_path):
    repo, patch, helpers, revision = _make_fixture(tmp_path)
    helpers["reference_cache.py"].write_text("LOCAL_HELPER = True\n", encoding="utf-8")
    before_status = _git(repo, "status", "--porcelain")

    result = _run("prepare", repo, patch, helpers, revision)

    assert result.returncode != 0
    assert "does not match patch" in result.stderr
    assert _git(repo, "status", "--porcelain") == before_status
    assert (repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    for name in HELPERS:
        assert (repo / f"selflift_runtime/{name}").exists()


def test_timeline_patch_recognizes_managed_helper_across_git_line_endings(tmp_path):
    repo, patch, helpers, revision = _make_fixture(tmp_path)
    assert _run("apply", repo, patch, helpers, revision).returncode == 0
    managed_helper = repo / "selflift_runtime" / "checkpoint_store.py"
    managed_helper.write_bytes(helpers["checkpoint_store.py"].read_bytes())
    helpers["checkpoint_store.py"].write_bytes(
        helpers["checkpoint_store.py"].read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    )

    result = _run("apply", repo, patch, helpers, revision)

    assert result.returncode == 0, result.stderr
    assert managed_helper.read_bytes().replace(b"\r\n", b"\n") == helpers["checkpoint_store.py"].read_bytes().replace(b"\r\n", b"\n")


def test_timeline_patch_preserves_setup_generated_alias_during_install_and_rerun(tmp_path):
    repo, patch, helpers, revision = _make_fixture(tmp_path)
    assert _run("prepare", repo, patch, helpers, revision).returncode == 0
    alias = repo / "example_workflows" / "h3_timeline_director.json"
    alias.parent.mkdir()
    alias.write_bytes(b'{"nodes": [], "user_notes": "preserve this alias"}\n')
    before = alias.read_bytes()

    for mode in ("prepare", "apply", "apply", "prepare", "apply"):
        result = _run(mode, repo, patch, helpers, revision)
        assert result.returncode == 0, result.stderr
        assert alias.read_bytes() == before


def test_timeline_patch_refuses_extra_hunks_in_a_managed_file(tmp_path):
    repo, patch, helpers, revision = _make_fixture(tmp_path)
    assert _run("apply", repo, patch, helpers, revision).returncode == 0
    (repo / "__init__.py").write_text("VALUE = 2\nUSER_CHANGE = True\n", encoding="utf-8")

    result = _run("prepare", repo, patch, helpers, revision)

    assert result.returncode != 0
    assert "USER_CHANGE" in (repo / "__init__.py").read_text(encoding="utf-8")


def test_timeline_patch_apply_requires_the_exact_base_revision(tmp_path):
    repo, patch, helpers, revision = _make_fixture(tmp_path)
    (repo / "__init__.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(repo, "add", "__init__.py")
    _git(repo, "commit", "-qm", "different base")
    before = _git(repo, "rev-parse", "HEAD")

    result = _run("apply", repo, patch, helpers, revision)

    assert result.returncode != 0
    assert _git(repo, "rev-parse", "HEAD") == before
    assert (repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 3\n"


def test_setup_fetches_patch_from_its_resolved_support_revision():
    text = (ROOT / "scripts" / "setupp_h3_studio.sh").read_text(encoding="utf-8")
    assert 'H3_SETUP_SUPPORT_REV="${H3_SETUP_SUPPORT_REV:-28f4bb42303771edd7c59f26f249fbd2b15c4b84}"' in text
    assert "$H3_SETUP_SUPPORT_REV/patches/timeline-director/two-phase-checkpoints.patch" in text
    assert "$H3_SETUP_SUPPORT_REV/patches/timeline-director/reference_cache.py" in text
    assert "raw.githubusercontent.com/halsn/vast_setup_script/main/patches" not in text


def test_timeline_patch_upgrade_accepts_exact_previous_diff_and_helper(tmp_path, monkeypatch):
    legacy_repo, _, legacy_helpers, revision = _make_fixture(tmp_path / "legacy", legacy=True)
    _, current_patch, current_helpers, _ = _make_fixture(tmp_path / "current")
    spec = importlib.util.spec_from_file_location("patch_tool_legacy", PATCH_TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    previous_diff = tool._git(
        legacy_repo, "-c", "core.autocrlf=true", "diff", "--no-ext-diff", "--binary"
    ).replace(b"\r\n", b"\n")
    monkeypatch.setattr(tool, "PREVIOUS_PATCH_SHA256", {hashlib.sha256(previous_diff).hexdigest()})
    monkeypatch.setattr(tool, "PREVIOUS_HELPER_SHA256", {hashlib.sha256(legacy_helpers["checkpoint_store.py"].read_bytes()).hexdigest()})

    tool.prepare(legacy_repo, current_patch, current_helpers, revision, REMOTE)

    assert tool._status(legacy_repo) == []
    assert not (legacy_repo / LEGACY_HELPER).exists()
    for path in ("__init__.py", "minimax_h3_finite_segments.py", "selflift_runtime/nodes.py"):
        assert (legacy_repo / path).read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (legacy_repo / "minimax_h3_timeline_director.py").exists()


def test_timeline_patch_refuses_previous_diff_with_edited_helper(tmp_path, monkeypatch):
    legacy_repo, _, legacy_helpers, revision = _make_fixture(tmp_path / "legacy", legacy=True)
    _, current_patch, current_helpers, _ = _make_fixture(tmp_path / "current")
    spec = importlib.util.spec_from_file_location("patch_tool_legacy_edit", PATCH_TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    previous_diff = tool._git(
        legacy_repo, "-c", "core.autocrlf=true", "diff", "--no-ext-diff", "--binary"
    ).replace(b"\r\n", b"\n")
    monkeypatch.setattr(tool, "PREVIOUS_PATCH_SHA256", {hashlib.sha256(previous_diff).hexdigest()})
    monkeypatch.setattr(tool, "PREVIOUS_HELPER_SHA256", {hashlib.sha256(legacy_helpers["checkpoint_store.py"].read_bytes()).hexdigest()})
    (legacy_repo / LEGACY_HELPER).write_text("local edit\n", encoding="utf-8")

    with pytest.raises(tool.PatchError, match="Refusing"):
        tool.prepare(legacy_repo, current_patch, current_helpers, revision, REMOTE)

    assert (legacy_repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (legacy_repo / LEGACY_HELPER).read_text(encoding="utf-8") == "local edit\n"


@pytest.mark.parametrize("user_edit", [False, True])
def test_timeline_patch_upgrade_accepts_only_the_audited_previous_diff(tmp_path, monkeypatch, user_edit):
    repo, _, legacy_helpers, revision = _make_fixture(tmp_path / "legacy", legacy=True)
    _, patch, helpers, _ = _make_fixture(tmp_path / "current")
    spec = importlib.util.spec_from_file_location("patch_tool_tracked_upgrade", PATCH_TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    previous_diff = tool._git(repo, "-c", "core.autocrlf=true", "diff", "--no-ext-diff", "--binary").replace(b"\r\n", b"\n")
    monkeypatch.setattr(tool, "PREVIOUS_PATCH_SHA256", {hashlib.sha256(previous_diff).hexdigest()})
    monkeypatch.setattr(tool, "PREVIOUS_HELPER_SHA256", {hashlib.sha256(legacy_helpers["checkpoint_store.py"].read_bytes()).hexdigest()})
    if user_edit:
        (repo / "__init__.py").write_text("VALUE = 2\nUSER_CHANGE = True\n", encoding="utf-8")
        with pytest.raises(tool.PatchError, match="Refusing"):
            tool.prepare(repo, patch, helpers, revision, REMOTE)
        assert "USER_CHANGE" in (repo / "__init__.py").read_text(encoding="utf-8")
    else:
        tool.prepare(repo, patch, helpers, revision, REMOTE)
        assert (repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        assert not (repo / LEGACY_HELPER).exists()

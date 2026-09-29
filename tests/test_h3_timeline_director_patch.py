from __future__ import annotations

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATCH_TOOL = ROOT / "scripts" / "h3_timeline_director_patch.py"
REMOTE = "https://github.com/Songssx/ComfyUI-MiniMaxH3-TimelineDirector.git"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _make_fixture(tmp_path: Path):
    repo = tmp_path / "plugin"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    tracked_paths = (
        "__init__.py",
        "minimax_h3_finite_segments.py",
        "selflift_runtime/nodes.py",
    )
    for path in tracked_paths:
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", *tracked_paths)
    _git(repo, "commit", "-qm", "pinned base")
    revision = _git(repo, "rev-parse", "HEAD")
    _git(repo, "remote", "add", "origin", REMOTE)

    helper = tmp_path / "checkpoint_store.py"
    helper.write_text("MANAGED_HELPER = True\n", encoding="utf-8")
    for path in tracked_paths:
        (repo / path).write_text("VALUE = 2\n", encoding="utf-8")
    managed_helper = repo / "selflift_runtime" / "checkpoint_store.py"
    managed_helper.parent.mkdir(exist_ok=True)
    managed_helper.write_bytes(helper.read_bytes())
    _git(repo, "add", "-N", "selflift_runtime/checkpoint_store.py")
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
    return repo, patch, helper, revision


def _run(mode: str, repo: Path, patch: Path, helper: Path, revision: str):
    return subprocess.run(
        [
            sys.executable,
            str(PATCH_TOOL),
            mode,
            "--repo",
            str(repo),
            "--patch",
            str(patch),
            "--helper",
            str(helper),
            "--revision",
            revision,
            "--remote",
            REMOTE,
        ],
        capture_output=True,
        text=True,
    )


def test_timeline_patch_install_and_rerun_are_idempotent(tmp_path):
    repo, patch, helper, revision = _make_fixture(tmp_path)

    prepared = _run("prepare", repo, patch, helper, revision)
    assert prepared.returncode == 0, prepared.stderr
    applied = _run("apply", repo, patch, helper, revision)
    assert applied.returncode == 0, applied.stderr
    assert (repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert (repo / "selflift_runtime/checkpoint_store.py").read_bytes() == helper.read_bytes()

    rerun = _run("apply", repo, patch, helper, revision)
    assert rerun.returncode == 0, rerun.stderr
    reverse = _run("prepare", repo, patch, helper, revision)
    assert reverse.returncode == 0, reverse.stderr
    assert (repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (repo / "selflift_runtime/checkpoint_store.py").exists()
    assert _run("prepare", repo, patch, helper, revision).returncode == 0


def test_timeline_patch_refuses_unrelated_dirty_state_without_removing_it(tmp_path):
    repo, patch, helper, revision = _make_fixture(tmp_path)
    assert _run("apply", repo, patch, helper, revision).returncode == 0
    user_file = repo / "user-change.txt"
    user_file.write_text("keep me\n", encoding="utf-8")

    result = _run("prepare", repo, patch, helper, revision)

    assert result.returncode != 0
    assert user_file.read_text(encoding="utf-8") == "keep me\n"
    assert (repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_timeline_patch_refuses_extra_hunks_in_a_managed_file(tmp_path):
    repo, patch, helper, revision = _make_fixture(tmp_path)
    assert _run("apply", repo, patch, helper, revision).returncode == 0
    (repo / "__init__.py").write_text("VALUE = 2\nUSER_CHANGE = True\n", encoding="utf-8")

    result = _run("prepare", repo, patch, helper, revision)

    assert result.returncode != 0
    assert "USER_CHANGE" in (repo / "__init__.py").read_text(encoding="utf-8")


def test_timeline_patch_apply_requires_the_exact_base_revision(tmp_path):
    repo, patch, helper, revision = _make_fixture(tmp_path)
    (repo / "__init__.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(repo, "add", "__init__.py")
    _git(repo, "commit", "-qm", "different base")
    before = _git(repo, "rev-parse", "HEAD")

    result = _run("apply", repo, patch, helper, revision)

    assert result.returncode != 0
    assert _git(repo, "rev-parse", "HEAD") == before
    assert (repo / "__init__.py").read_text(encoding="utf-8") == "VALUE = 3\n"


def test_setup_fetches_patch_from_its_resolved_support_revision():
    text = (ROOT / "scripts" / "setupp_h3_studio.sh").read_text(encoding="utf-8")
    assert 'H3_SETUP_SUPPORT_REV="${H3_SETUP_SUPPORT_REV:-9b70ec88127350526c94de076f2e46861e93502f}"' in text
    assert "$H3_SETUP_SUPPORT_REV/patches/timeline-director/two-phase-checkpoints.patch" in text
    assert "raw.githubusercontent.com/halsn/vast_setup_script/main/patches" not in text

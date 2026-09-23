from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Thread


ROOT = Path(__file__).resolve().parents[1]


def _bash_executable() -> str:
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    bash = shutil.which("bash")
    if not bash:
        raise RuntimeError("Bash is required for H3 setup behavior tests")
    return bash


BASH = _bash_executable()


def _bash_path(path: Path) -> str:
    if os.name != "nt":
        return str(path)
    result = subprocess.run(
        [BASH, "-c", 'cygpath -u "$1"', "path", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _run_bash(script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, "-c", script, "h3-test", *args],
        cwd=ROOT,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
    )


def _git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _create_pinned_repository(tmp_path: Path) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git("init", "--quiet", cwd=source)
    (source / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")
    _git("add", "__init__.py", cwd=source)
    _git(
        "-c",
        "user.name=H3 test",
        "-c",
        "user.email=h3-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
        cwd=source,
    )
    revision = _git("rev-parse", "HEAD", cwd=source)
    (source / "latest.txt").write_text("newer remote state\n", encoding="utf-8")
    _git("add", "latest.txt", cwd=source)
    _git(
        "-c",
        "user.name=H3 test",
        "-c",
        "user.email=h3-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "newer than pin",
        cwd=source,
    )
    bare = tmp_path / "node.git"
    _git("clone", "--quiet", "--bare", str(source), str(bare), cwd=tmp_path)
    return bare, revision


def _install_script(common_script: Path, comfy_dir: Path, repository: Path, revision: str) -> str:
    return _run_bash(
        'source "$1"; COMFY_DIR="$2"; '
        'h3_profile_install_pinned_node "EnhancerNode" "$3" "$4"',
        _bash_path(common_script),
        _bash_path(comfy_dir),
        _bash_path(repository),
        revision,
    )


def test_pinned_node_installs_exact_commit_reruns_and_refuses_dirty_targets(tmp_path: Path) -> None:
    repository, revision = _create_pinned_repository(tmp_path)
    common_script = ROOT / "scripts" / "h3_profile_common.sh"
    comfy_dir = tmp_path / "comfy"
    node_dir = comfy_dir / "custom_nodes" / "EnhancerNode"

    first = _install_script(common_script, comfy_dir, repository, revision)
    assert first.returncode == 0, first.stderr
    assert _git("rev-parse", "HEAD", cwd=node_dir) == revision
    assert not (node_dir / "latest.txt").exists()

    second = _install_script(common_script, comfy_dir, repository, revision)
    assert second.returncode == 0, second.stderr
    assert _git("rev-parse", "HEAD", cwd=node_dir) == revision

    dirty_file = node_dir / "__init__.py"
    dirty_file.write_text("local change must survive\n", encoding="utf-8")
    refused = _install_script(common_script, comfy_dir, repository, revision)
    assert refused.returncode != 0
    assert "dirty" in refused.stderr.lower()
    assert dirty_file.read_text(encoding="utf-8") == "local change must survive\n"


def test_pinned_node_refuses_non_git_target_without_overwriting_it(tmp_path: Path) -> None:
    repository, revision = _create_pinned_repository(tmp_path)
    common_script = ROOT / "scripts" / "h3_profile_common.sh"
    comfy_dir = tmp_path / "comfy"
    target = comfy_dir / "custom_nodes" / "EnhancerNode"
    target.mkdir(parents=True)
    marker = target / "keep.txt"
    marker.write_text("do not replace\n", encoding="utf-8")

    result = _install_script(common_script, comfy_dir, repository, revision)

    assert result.returncode != 0
    assert "not a git checkout" in result.stderr.lower()
    assert marker.read_text(encoding="utf-8") == "do not replace\n"


def test_enhancer_readiness_requires_both_object_info_nodes() -> None:
    class Handler(BaseHTTPRequestHandler):
        payload: dict[str, object] = {}

        def do_GET(self) -> None:
            if self.path != "/object_info":
                self.send_error(404)
                return
            body = json.dumps(self.payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Handler.payload = {
        "MiniMaxH3PromptEnhancerT8": {},
        "T8ShowText": {},
    }
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    common_script = ROOT / "scripts" / "h3_profile_common.sh"
    env = {
        "COMFY_PORT": str(server.server_port),
        "COMFY_PYTHON": _bash_path(Path(sys.executable)),
    }
    try:
        ready = _run_bash(
            'source "$1"; h3_profile_verify_t8_prompt_enhancer',
            _bash_path(common_script),
            env=env,
        )
        assert ready.returncode == 0, ready.stderr

        Handler.payload = {"MiniMaxH3PromptEnhancerT8": {}}
        missing = _run_bash(
            'source "$1"; h3_profile_verify_t8_prompt_enhancer',
            _bash_path(common_script),
            env=env,
        )
        assert missing.returncode != 0
        assert "T8ShowText" in missing.stderr
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_native_comfyui_minimum_updates_032_but_default_stays_at_030() -> None:
    base_script = ROOT / "scripts" / "h3_comfui_base.sh"
    script = r'''
H3_BOOTSTRAP_LIB_ONLY=1
source "$1"
COMFY_DIR="/tmp/comfy-fixture"
use_vast_comfy_base() { return 0; }
get_comfyui_version() { printf '%s\n' "$CURRENT_VERSION"; }
update_git_checkout() { printf 'UPDATE:%s:%s\n' "$1" "$2"; }
CURRENT_VERSION="0.32.1"
H3_MIN_COMFYUI_VERSION="0.33.0"
update_comfyui
CURRENT_VERSION="0.30.0"
unset H3_MIN_COMFYUI_VERSION
update_comfyui
'''

    result = _run_bash(script, _bash_path(base_script))

    assert result.returncode == 0, result.stderr
    assert "UPDATE:/tmp/comfy-fixture:ComfyUI" in result.stdout
    assert result.stdout.count("UPDATE:") == 1
    assert "0.30.0" in result.stderr

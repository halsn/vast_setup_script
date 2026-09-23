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

NODE_NAME = "comfyui-minimax-h3-blockcache-T8"
NODE_REPO = "https://github.com/T8mars/comfyui-minimax-h3-blockcache-T8.git"
NODE_REVISION = "36336dcee1ecb49a5ee98426456aeb353c8535bd"


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


def test_native_profile_pins_blockcache_only_for_native_setup() -> None:
    native_path = ROOT / "scripts" / "setupp_h3_comfui.sh"
    native = native_path.read_text(encoding="utf-8")

    assert f'T8_BLOCKCACHE_NODE="{NODE_NAME}"' in native
    assert f'T8_BLOCKCACHE_REPO="{NODE_REPO}"' in native
    assert f'T8_BLOCKCACHE_REV="{NODE_REVISION}"' in native
    install = native.index(
        'h3_profile_install_pinned_node "$T8_BLOCKCACHE_NODE" "$T8_BLOCKCACHE_REPO" "$T8_BLOCKCACHE_REV"'
    )
    finish = native.index("h3_profile_finish")
    verify = native.index("h3_profile_verify_t8_blockcache")
    assert install < finish < verify

    for relative in (
        "scripts/setupp_h3_comfui_turbo.sh",
        "scripts/setupp_h3_comfui_cache.sh",
        "scripts/setupp_h3_comfui_pdd.sh",
        "scripts/setupp_h3_comfui_vdn.sh",
        "scripts/setupp_h3_comfui_fasth3.sh",
        "scripts/setupp_h3_studio.sh",
    ):
        assert NODE_REPO not in (ROOT / relative).read_text(encoding="utf-8")


def test_blockcache_readiness_requires_its_object_info_node() -> None:
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
    Handler.payload = {"MiniMaxH3BlockCacheT8": {}}
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    common_script = ROOT / "scripts" / "h3_profile_common.sh"
    env = {
        "COMFY_PORT": str(server.server_port),
        "COMFY_PYTHON": sys.executable,
    }
    try:
        ready = _run_bash(
            'source "$1"; h3_profile_verify_t8_blockcache',
            _bash_path(common_script),
            env=env,
        )
        assert ready.returncode == 0, ready.stderr

        Handler.payload = {}
        missing = _run_bash(
            'source "$1"; h3_profile_verify_t8_blockcache',
            _bash_path(common_script),
            env=env,
        )
        assert missing.returncode != 0
        assert "MiniMaxH3BlockCacheT8" in missing.stderr
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

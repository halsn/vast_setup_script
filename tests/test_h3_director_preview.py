import hashlib
import io
from pathlib import Path
import sys

import pytest

from tests.test_h3_runtime_updates import bash

ROOT = Path(__file__).resolve().parents[1]
STUDIO = ROOT / "scripts/setupp_h3_studio.sh"


def preview_download_source():
    text = STUDIO.read_text(encoding="utf-8")
    assert "h3_studio_install_preview_decoder() {" in text
    function = text.split("h3_studio_install_preview_decoder() {", 1)[1].split("\n}\n", 1)[0]
    return function.split("<<'PY'\n", 1)[1].split("\nPY", 1)[0]


def test_preview_method_is_scoped_to_studio():
    studio = STUDIO.read_text(encoding="utf-8")
    assert 'H3_PREVIEW_METHOD="${H3_PREVIEW_METHOD:-taesd}"' in studio
    assert "taesd" in bash('H3_PREVIEW_METHOD=taesd\nexpected_runtime_flags 32768')
    assert "none" in bash("expected_runtime_flags 32768")
    assert "--fast" not in bash("H3_PREVIEW_METHOD=taesd\nexpected_runtime_flags 32768")


def test_preview_replaces_existing_none_in_wrapper_and_supervisor(tmp_path):
    wrapper = tmp_path / "launch.sh"
    config = tmp_path / "supervisor.conf"
    wrapper.write_text("#!/bin/bash\npython3 main.py --preview-method none --port 8188\n")
    config.write_text("[program:comfy]\ncommand=python3 main.py --preview-method=none --port 8188\n")
    interpreter = Path(sys.executable).as_posix()
    bash(f'\npython3() {{ "{interpreter}" "$@"; }}\nH3_PREVIEW_METHOD=taesd\npatch_comfy_command_file "{wrapper.as_posix()}" 32768 0\npatch_supervisor_config "{config.as_posix()}" comfy 32768 0\n')
    for path in [wrapper, config]:
        text = path.read_text()
        assert "--preview-method taesd" in text
        assert "--preview-method none" not in text
        assert "--preview-method=none" not in text
        assert text.count("--preview-method") == 1
        assert "--port 8188" in text
    first = wrapper.read_text()
    bash(f'\npython3() {{ "{interpreter}" "$@"; }}\nH3_PREVIEW_METHOD=taesd\npatch_comfy_command_file "{wrapper.as_posix()}" 32768 0\n')
    assert wrapper.read_text() == first


def test_preview_decoder_is_pinned_and_final_vae_is_preserved():
    text = STUDIO.read_text(encoding="utf-8")
    assert "62f7591f59dfbb4c3c02b7a621d180a9eeaba26c/safetensors/taeh3.safetensors" in text
    assert "4fd022bfcab08772fe0536b17ea1a3bbb5625be11e397868d1c5d891863d4c13" in text
    assert "22709752" in text
    assert "$COMFY_DIR/models/vae_approx/taeh3.safetensors" in text
    main = text.split("main_studio() {", 1)[1]
    assert main.index("h3_studio_install_preview_decoder") < main.index("h3_profile_finish")


def test_preview_preserves_quoted_supervisor_command_and_is_idempotent(tmp_path):
    config = tmp_path / "supervisor.conf"
    config.write_text("[program:comfy]\ncommand=bash -c 'python3 main.py --port 8188 --preview-method none'\n")
    interpreter = Path(sys.executable).as_posix()
    patch = f'\npython3() {{ "{interpreter}" "$@"; }}\nH3_PREVIEW_METHOD=taesd\npatch_supervisor_config "{config.as_posix()}" comfy 32768 0\n'
    bash(patch)
    first = config.read_text()
    bash(patch)
    assert config.read_text() == first
    import shlex
    parts = shlex.split(first.split("command=", 1)[1])
    assert parts[:2] == ["bash", "-c"]
    assert "--preview-method taesd" in parts[2]
    assert first.count("--preview-method") == 1


def test_preview_updates_already_configured_wrapper(tmp_path):
    wrapper = tmp_path / "launch.sh"
    wrapper.write_text("python3 main.py --listen 0.0.0.0 --enable-cors-header '*' --reserve-vram 2 --preview-method none\n")
    interpreter = Path(sys.executable).as_posix()
    bash(f'\npython3() {{ "{interpreter}" "$@"; }}\nH3_PREVIEW_METHOD=taesd\npatch_comfy_command_file "{wrapper.as_posix()}" 32768 0\n')
    assert wrapper.read_text().endswith("--preview-method taesd\n")


def test_preview_removes_later_conflicting_supervisor_flag(tmp_path):
    config = tmp_path / "supervisor.conf"
    config.write_text("[program:comfy]\ncommand=python3 main.py --preview-method taesd --port 8188 --preview-method none\n")
    interpreter = Path(sys.executable).as_posix()
    bash(f'\npython3() {{ "{interpreter}" "$@"; }}\nH3_PREVIEW_METHOD=taesd\npatch_supervisor_config "{config.as_posix()}" comfy 32768 0\n')
    text = config.read_text()
    assert text.count("--preview-method") == 1
    assert "--preview-method taesd" in text
    assert "--port 8188" in text


def test_mock_decoder_download_is_verified_atomic_and_idempotent(tmp_path, monkeypatch):
    import urllib.request

    payload = b"mock tiny decoder"
    target = tmp_path / "taeh3.safetensors"
    calls = []
    def download(*args, **kwargs):
        calls.append(args)
        return io.BytesIO(payload)
    monkeypatch.setattr(urllib.request, "urlopen", download)
    monkeypatch.setattr(sys, "argv", ["download", str(target), "https://example.invalid/pinned", hashlib.sha256(payload).hexdigest(), str(len(payload))])
    source = preview_download_source()
    exec(compile(source, "mock_preview_download", "exec"), {})
    exec(compile(source, "mock_preview_download", "exec"), {})
    assert target.read_bytes() == payload
    assert len(calls) == 1

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"bad download"))
    target.write_bytes(b"existing invalid artifact")
    with pytest.raises((RuntimeError, SystemExit), match="(?i)(checksum|sha|size|integrity)"):
        exec(compile(source, "mock_preview_download", "exec"), {})
    assert target.read_bytes() == b"existing invalid artifact"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["taeh3.safetensors"]

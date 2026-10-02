import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.test_h3_profiles import BASH

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "scripts/h3_comfui_base.sh"
PIN = "6b747c0428c343e1417219641db93a4fb7cb69ae"
SUPPORT_PIN = "638b300291eb0b2f99a0318c705fd139611fa4fb"
VAE = "minimax_h3_video_vae_int8_convrot.safetensors"


def bash(body):
    source = BASE.as_posix()
    result = subprocess.run(
        [BASH, "-c", f'H3_BOOTSTRAP_LIB_ONLY=1\nsource "{source}"\n' + body],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_base_uses_pinned_current_core_without_upgrading_torch():
    result = bash('printf "%s|%s" "$H3_COMFYUI_REF" "$H3_MIN_COMFYUI_VERSION"')
    assert result == f"{PIN}|0.38.0"
    text = BASE.read_text(encoding="utf-8")
    assert "--no-deps" in text


def test_attention_installer_download_is_pinned_with_studio_companions():
    assert f'${{H3_SETUP_SUPPORT_REV:-{SUPPORT_PIN}}}/scripts/h3_attention_runtime.py' in BASE.read_text()
    studio = (ROOT / "scripts/setupp_h3_studio.sh").read_text(encoding="utf-8")
    assert f'H3_SETUP_SUPPORT_REV="${{H3_SETUP_SUPPORT_REV:-{SUPPORT_PIN}}}"' in studio


def test_sage_version_override_upgrades_existing_vast_package():
    output = bash('''
H3_SAGE_VERSION=2.2.0
COMFY_PYTHON=fake_python
TEST_VERSION=1.0.6
use_vast_comfy_base() { return 0; }
fake_python() {
  if [[ "$*" == *"pip install"* ]]; then
    printf 'INSTALL %s\\n' "$*"
    TEST_VERSION=2.2.0
  elif [[ "$*" == *"importlib.metadata"* ]]; then printf '%s\\n' "$TEST_VERSION";
  fi
}
verify_sageattention_kernel() { printf 'KERNEL_VERIFIED\\n'; }
install_sageattention
''')
    assert "sageattention==2.2.0" in output
    assert "--no-deps" in output
    assert "KERNEL_VERIFIED" in output


def test_matching_sage_still_verifies_a_real_kernel():
    output = bash('''
H3_SAGE_VERSION=2.2.0
COMFY_PYTHON=fake_python
use_vast_comfy_base() { return 0; }
fake_python() {
  [[ "$*" != *"pip install"* ]] || return 99
  [[ "$*" != *"importlib.metadata"* ]] || printf '2.2.0\\n'
}
verify_sageattention_kernel() { printf 'KERNEL_VERIFIED\\n'; }
install_sageattention
''')
    assert output == "KERNEL_VERIFIED\n"


def test_sage_import_success_does_not_hide_kernel_failure():
    source = BASE.as_posix()
    body = f'''H3_BOOTSTRAP_LIB_ONLY=1
source "{source}"
COMFY_PYTHON=fake_python
fake_python() {{ [[ "$*" != *importlib.metadata* ]] || printf '2.2.0\\n'; }}
verify_sageattention_kernel() {{ return 1; }}
install_sageattention
'''
    run = subprocess.run([BASH, "-c", body], capture_output=True, text=True)
    assert run.returncode != 0
    assert "verification failed" in run.stderr


def test_core_update_checks_out_selected_commit_and_is_idempotent():
    for current in ("old-core", PIN):
        output = bash(f'''
COMFY_DIR=/tmp/comfy
git() {{
  if [[ "$*" == *"rev-parse HEAD" ]]; then printf '{current}\\n';
  elif [[ "$*" == *"rev-parse"* ]]; then printf '{PIN}\\n';
  elif [[ "$*" == *"status --porcelain" ]]; then :;
  else printf 'GIT %s\\n' "$*"; fi
}}
update_comfyui
printf 'UPDATED=%s\\n' "$H3_COMFYUI_CORE_UPDATED"
''')
        if current == PIN:
            assert "GIT" not in output
            assert "UPDATED=0" in output
        else:
            assert f"fetch --depth=1 origin {PIN}" in output
            assert "checkout --detach FETCH_HEAD" in output
            assert "UPDATED=1" in output


def test_dependency_repair_runs_at_pinned_core_and_preserves_torch(tmp_path):
    (tmp_path / "requirements.txt").write_text("torch\ncomfy-kitchen==0.2.36\n")
    output = bash(f'''
COMFY_DIR="{tmp_path.as_posix()}"
COMFY_PYTHON=fake_python
H3_COMFYUI_CORE_UPDATED=0
use_vast_comfy_base() {{ return 0; }}
fake_python() {{
  if [[ "$1" == "-" ]]; then
    cat >/dev/null
    printf 'torch==2.10.0+cu130\\n'
  elif [[ "$*" == *"pip install"* ]]; then
    printf 'INSTALL %s\\n' "$*"
    [[ "$*" == *" -c "* ]] || return 99
  fi
}}
update_python_dependencies
''')
    assert output.count("INSTALL") == 2
    assert "-r /tmp/h3-comfyui-requirements.txt" in output


def test_int8_vae_has_pinned_metadata_and_fp16_is_retained():
    output = bash(f'''model_manifest
model_expected_bytes "vae/{VAE}"
model_expected_sha256 "vae/{VAE}"
model_revision "vae/{VAE}"
''')
    assert f"vae/{VAE}" in output
    assert "vae/minimax_h3_video_vae_fp16.safetensors" in output
    assert "2811065184" in output
    assert "52a2c8c73583c86e4f41cdcce3a6ad0ea562987bc0bf3d60a0cef5f5c8e60c0e" in output
    assert "e5eb578a89295337b8ff433a035929ce0279e0b6" in output
    models = json.loads((ROOT / "config/models.json.example").read_text())["models"]
    item = next(m for m in models if m["filename"] == VAE)
    assert item["size_bytes"] == 2811065184
    assert all("e5eb578a89295337b8ff433a035929ce0279e0b6" in u for u in item["urls"])


def test_int8_is_default_and_fp16_remains_an_explicit_choice():
    assert bash('printf "%s" "$H3_VIDEO_VAE_NAME"') == VAE
    assert bash(f'H3_VIDEO_VAE_VARIANT=fp16 source "{BASE.as_posix()}"\nprintf "%s" "$H3_VIDEO_VAE_NAME"') == "minimax_h3_video_vae_fp16.safetensors"
    text = BASE.read_text()
    assert 'H3_VIDEO_VAE_VARIANT' in text
    assert '"minimax_h3_video_vae_fp16.safetensors": video_vae_name' in text


def load_guard():
    path = ROOT / "scripts/h3_attention_runtime.py"
    assert path.is_file(), "The common Sage guard and live status installer is missing"
    spec = importlib.util.spec_from_file_location("attention_update", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SAGE_SOURCE = '''
import logging
def attention_sage(q, k, v, heads, mask=None, attn_precision=None, skip_reshape=False, skip_output_reshape=False, **kwargs):
    if kwargs.get("low_precision_attention", True) is False:
        return attention_pytorch(q, k, v, heads, mask=mask, skip_reshape=skip_reshape, skip_output_reshape=skip_output_reshape, **kwargs)
    b, _, _, dim_head = q.shape
    tensor_layout = "HND"
    sage_kwargs = {"is_causal": False, "tensor_layout": tensor_layout}
    try:
        out = sageattn(q, k, v, **sage_kwargs)
    except Exception as e:
        logging.error(str(e))
        return attention_pytorch(q, k, v, heads, skip_reshape=True, **kwargs)
    return out
'''


class Tensor:
    shape = (1, 56, 120000, 128)

    def __init__(self, contiguous=False, elements=860160000):
        self.canonical = contiguous
        self.elements = elements

    def numel(self):
        return self.elements

    def contiguous(self):
        return Tensor(True, self.elements)


def guarded_function(monkeypatch, sage=None):
    module = load_guard()
    events = []
    import sys
    import types
    status = types.ModuleType("comfy.h3_attention_status")
    status.record_attention = lambda *args: events.append(args)
    monkeypatch.setitem(sys.modules, "comfy.h3_attention_status", status)
    env = {"sageattn": sage or (lambda *a, **k: a), "attention_pytorch": lambda *a, **k: "sdpa"}
    exec(module.patch_attention_source(SAGE_SOURCE), env)
    return env["attention_sage"], events


def test_common_guard_passes_contiguous_inputs_and_preserves_sage(monkeypatch):
    function, events = guarded_function(monkeypatch)
    inputs = tuple(Tensor() for _ in range(3))
    output = function(*inputs, 56, skip_reshape=True)
    assert all(t.canonical for t in output)
    assert all(not t.canonical for t in inputs)
    assert events[-1] == ("sage", None)


def test_common_guard_avoids_int32_overflow_before_launch(monkeypatch):
    function, events = guarded_function(monkeypatch, lambda *a, **k: pytest.fail("unsafe launch"))
    assert function(*(Tensor(elements=2**31) for _ in range(3)), 56) == "sdpa"
    assert events[-1][0] == "pytorch"
    assert "int32" in events[-1][1]


def test_common_guard_does_not_continue_after_illegal_access(monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError("CUDA error: an illegal memory access was encountered")
    function, events = guarded_function(monkeypatch, crash)
    with pytest.raises(RuntimeError, match="illegal memory access"):
        function(*(Tensor() for _ in range(3)), 56)
    assert "illegal memory access" in events[-1][1]
    assert events[-1][0] == "cuda_error"


def test_common_guard_records_precision_and_nonfatal_fallbacks(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("unsupported kernel dtype")
    function, events = guarded_function(monkeypatch, fail)
    inputs = tuple(Tensor() for _ in range(3))
    assert function(*inputs, 56, low_precision_attention=False) == "sdpa"
    assert events[-1] == ("pytorch", "precision or mask requires PyTorch")
    assert function(*inputs, 56) == "sdpa"
    assert events[-1] == ("pytorch", "unsupported kernel dtype")


def test_common_guard_patch_is_idempotent_and_rejects_changed_source():
    module = load_guard()
    once = module.patch_attention_source(SAGE_SOURCE)
    assert module.patch_attention_source(once) == once
    ast.parse(once)
    with pytest.raises(ValueError):
        module.patch_attention_source("def attention_sage(q, k, v): return q\n")


def test_setup_installs_guard_and_readiness_reports_live_attention():
    assert "install_attention_runtime" in BASE.read_text()
    readiness = (ROOT / "runtime/worker_gateway.py").read_text()
    assert '"/h3/runtime_status"' in readiness
    assert 'payload["attention"]' in readiness
    assert 'payload["backend"]' in readiness


def test_alias_vae_selection_preserves_other_models(tmp_path):
    workflow = tmp_path / "alias.json"
    workflow.write_text(json.dumps({"nodes": [{"widgets_values": [
        "minimax_h3_video_vae_fp16.safetensors", "minimax_h3_audio_vae_fp32.safetensors"]}]}))
    common = (ROOT / "scripts/h3_profile_common.sh").as_posix()
    python = Path(os.sys.executable).as_posix()
    output = bash(f'''source "{common}"
COMFY_PYTHON="{python}"
H3_VIDEO_VAE_NAME={VAE}
h3_profile_patch_video_vae "{workflow.as_posix()}"
''')
    assert not output
    values = json.loads(workflow.read_text())["nodes"][0]["widgets_values"]
    assert values == [VAE, "minimax_h3_audio_vae_fp32.safetensors"]


def test_native_prompt_defaults_to_int8_and_accepts_fp16_without_changing_sampling():
    from runtime.workflow_builder import build_h3_prompt, WorkflowBuildError
    args = ("h3_t2v", "fox", "", {"width": 512, "height": 512, "frames": 124, "seed": 1}, [])
    old = build_h3_prompt(*args)
    assert old["video_vae"]["inputs"]["vae_name"] == VAE
    selected = build_h3_prompt(*args, video_vae_name="minimax_h3_video_vae_fp16.safetensors")
    assert selected["video_vae"]["inputs"]["vae_name"] == "minimax_h3_video_vae_fp16.safetensors"
    selected["video_vae"] = old["video_vae"]
    assert selected == old
    with pytest.raises(WorkflowBuildError):
        build_h3_prompt(*args, video_vae_name="unknown.safetensors")


def test_installer_applies_guard_and_writes_selected_vae(tmp_path):
    module = load_guard()
    source = tmp_path / "comfy/ldm/modules/attention.py"
    source.parent.mkdir(parents=True)
    source.write_text(SAGE_SOURCE)
    module.install(tmp_path, video_vae_name=VAE)
    once = source.read_text()
    module.install(tmp_path, video_vae_name=VAE)
    assert source.read_text() == once
    assert json.loads((tmp_path / ".h3-video-vae.json").read_text()) == {"video_vae": VAE}
    assert (tmp_path / "custom_nodes/ComfyUI-H3-RuntimeStatus/__init__.py").is_file()


def test_installer_defaults_to_int8(tmp_path):
    module = load_guard()
    source = tmp_path / "comfy/ldm/modules/attention.py"
    source.parent.mkdir(parents=True)
    source.write_text(SAGE_SOURCE)
    module.install(tmp_path)
    assert json.loads((tmp_path / ".h3-video-vae.json").read_text()) == {"video_vae": VAE}


def test_worker_defaults_to_int8_but_preserves_saved_fp16_selection(tmp_path):
    from runtime.worker_gateway import _selected_video_vae
    assert _selected_video_vae(tmp_path) == VAE
    selected = "minimax_h3_video_vae_fp16.safetensors"
    (tmp_path / ".h3-video-vae.json").write_text(json.dumps({"video_vae": selected}))
    assert _selected_video_vae(tmp_path) == selected


def test_status_does_not_claim_a_kernel_was_run_at_startup(monkeypatch):
    import sys
    import types
    module = load_guard()
    fake = types.SimpleNamespace(optimized_attention=types.SimpleNamespace(__name__="attention_sage"),
                                 REGISTERED_ATTENTION_FUNCTIONS={"sage": None, "pytorch": None})
    monkeypatch.setitem(sys.modules, "comfy", types.ModuleType("comfy"))
    monkeypatch.setitem(sys.modules, "comfy.ldm", types.ModuleType("comfy.ldm"))
    modules = types.ModuleType("comfy.ldm.modules")
    modules.attention = fake
    monkeypatch.setitem(sys.modules, "comfy.ldm.modules", modules)
    env = {"__file__": str(ROOT / "comfy/h3_attention_status.py")}
    exec(module.STATUS_SOURCE, env)
    before = env["runtime_status"]()
    assert before["configured_backend"] == "sage"
    assert before["last_backend"] is None
    env["record_attention"]("pytorch", "int32 guard")
    env["record_attention"]("sage", None)
    after = env["runtime_status"]()
    assert after["last_backend"] == "sage"
    assert after["last_fallback_reason"] == "int32 guard"
    assert after["fallback_count"] == 1

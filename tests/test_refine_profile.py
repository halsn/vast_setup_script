import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


NATIVE_SCRIPT = Path("scripts/setupp_h3_comfui.sh")
BASE_CORE = Path("scripts/h3_comfui_base.sh")
COMMON = Path("scripts/h3_profile_common.sh")
REFINE_SCRIPT = Path("scripts/setupp_h3_comfui_refine.sh")
REGISTRY = Path("config/deployment_profiles.json")
PROFILE_SCRIPTS = [
    NATIVE_SCRIPT,
    Path("scripts/setupp_h3_comfui_cache.sh"),
    Path("scripts/setupp_h3_comfui_fasth3.sh"),
    Path("scripts/setupp_h3_comfui_pdd.sh"),
    Path("scripts/setupp_h3_comfui_turbo.sh"),
    Path("scripts/setupp_h3_comfui_vdn.sh"),
]


def _bash_executable():
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    bash = shutil.which("bash")
    if not bash:
        raise RuntimeError("Bash is required to validate H3 setup scripts")
    return bash


BASH = _bash_executable()


def test_shared_h3_layer_installs_refine_and_packed_latent_persistence_by_default():
    text = COMMON.read_text(encoding="utf-8")

    assert 'H3_INSTALL_REFINE="${H3_INSTALL_REFINE:-1}"' in text
    assert "https://github.com/xmarre/Comfyui_Minimax_h3_latent_Upscaler-Plus.git" in text
    assert "620165a311de9b28a36260219fb5cd370a304e3c" in text
    assert "minimax_h3_latent_upscaler_3d_fp16.safetensors" in text
    assert "043e5a48e161610ef6c3ea974645220354d06fa618abca15f76d084812eb55c2" in text
    assert "https://github.com/MadPonyInteractive/ComfyUi-MpiNodes.git" in text
    assert "1de35a33827b125fe2adbc08df23266c465c032a" in text
    assert '"MpiSaveLatent"' in text
    assert '"MpiLoadLatent"' in text
    assert '"MinimaxH3LatentUpscaler3DRefineHandoff"' in text

    prepare = text[text.index("h3_profile_prepare_base() {") : text.index("h3_profile_install_requirements() {")]
    assert "h3_profile_install_refine_capability" in prepare
    finish = text[text.index("h3_profile_finish() {") :]
    assert "h3_profile_verify_refine_capability" in finish


def test_refine_can_only_be_disabled_explicitly():
    text = COMMON.read_text(encoding="utf-8")

    assert 'if [[ "${H3_INSTALL_REFINE:-1}" != "1" ]]' in text
    assert "Refine capability disabled by H3_INSTALL_REFINE=0" in text


def test_refine_download_uses_versioned_hf_path_and_keeps_local_checkpoint_name(tmp_path):
    fake_hub = tmp_path / "fake_hub"
    fake_hub.mkdir()
    (fake_hub / "huggingface_hub.py").write_text(
        "import os\n"
        "def hf_hub_download(*, repo_id, filename):\n"
        "    assert repo_id == 'LBH-123-AI/Minimax_h3_latent_Upscaler'\n"
        "    assert filename == 'minimax_h3_latent_upscaler_3d_conv_v1/minimax_h3_latent_upscaler_3d_conv_v1_fp16.safetensors'\n"
        "    return os.environ['FAKE_HF_FILE']\n",
        encoding="utf-8",
    )
    payload = b"refine checkpoint fixture"
    source = tmp_path / "downloaded.safetensors"
    source.write_bytes(payload)
    comfy_dir = tmp_path / "comfy"

    def bash_path(path):
        if os.name != "nt":
            return str(path)
        return subprocess.run(
            [BASH, "-c", 'cygpath -u "$1"', "bash", str(path)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    env = os.environ.copy()
    env["COMFY_DIR"] = bash_path(comfy_dir)
    env["COMFY_PYTHON"] = bash_path(Path(sys.executable))
    env["FAKE_HF_FILE"] = str(source)
    env["H3_TEST_REFINE_SHA256"] = hashlib.sha256(payload).hexdigest()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(fake_hub), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    harness = """set -Eeuo pipefail
source scripts/h3_profile_common.sh
h3_profile_ensure_hf() { :; }
REFINE_MODEL_SHA256="$H3_TEST_REFINE_SHA256"
h3_profile_install_refine_model
"""
    run = subprocess.run(
        [BASH, "-c", harness],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
    )

    assert run.returncode == 0, run.stdout + run.stderr
    installed = (
        comfy_dir
        / "models"
        / "latent_upscale_models"
        / "minimax_h3_latent_upscaler_3d_fp16.safetensors"
    )
    assert installed.read_bytes() == payload


def test_native_is_a_thin_profile_and_every_user_facing_profile_inherits_shared_capabilities():
    assert BASE_CORE.exists(), "internal H3 base core must be separated from user-facing deployment scripts"
    native = NATIVE_SCRIPT.read_text(encoding="utf-8")
    assert "h3_profile_prepare_base" in native
    assert "h3_profile_finish" in native

    for script in PROFILE_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        assert "h3_profile_prepare_base" in text, f"{script.name} must execute the shared H3 capability layer"

    common = COMMON.read_text(encoding="utf-8")
    assert "scripts/h3_comfui_base.sh" in common
    assert "scripts/setupp_h3_comfui.sh" not in common


def test_native_wrapper_preserves_base_help_and_version_cli():
    native = NATIVE_SCRIPT.read_text(encoding="utf-8")
    dispatch = native[native.index("main_native() {") : native.index('main_native "$@"')]

    assert '-h|--help|--version' in dispatch
    assert "h3_profile_load_base" in dispatch
    assert 'main "$@"' in dispatch
    assert dispatch.index("h3_profile_load_base") < dispatch.index('main "$@"')
    assert dispatch.index('main "$@"') < dispatch.index("h3_profile_prepare_base")


def test_refine_is_not_a_separate_deployment_profile():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    assert not REFINE_SCRIPT.exists()
    assert all(profile["id"] != "refine" for profile in registry["profiles"])

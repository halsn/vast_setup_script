import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCRIPTS = [
    "scripts/setupp_h3_comfui_turbo.sh",
    "scripts/setupp_h3_comfui_cache.sh",
    "scripts/setupp_h3_comfui_pdd.sh",
    "scripts/setupp_h3_comfui_vdn.sh",
    "scripts/setupp_h3_comfui_fasth3.sh",
]
STUDIO_SCRIPT = "scripts/setupp_h3_studio.sh"
NATIVE_SCRIPT = "scripts/setupp_h3_comfui.sh"
SCRIPTS = ["scripts/h3_profile_common.sh", *PROFILE_SCRIPTS, NATIVE_SCRIPT, STUDIO_SCRIPT]


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


def test_motion_context_studio_install_is_pinned_and_prepares_only_its_alias():
    text = (ROOT / STUDIO_SCRIPT).read_text()
    assert 'MOTION_CONTEXT_NODE_NAME="ComfyUI-H3-Motion-Context"' in text
    assert 'MOTION_CONTEXT_NODE_REPO="https://github.com/NikoDemon80/ComfyUI-H3-Motion-Context.git"' in text
    assert 'MOTION_CONTEXT_NODE_REV="5335715abe54c1a9bfbe3494da29aae3e8635ce3"' in text
    assert 'MOTION_CONTEXT_TEMPLATE_SOURCE="example_workflows/MiniMax H3 - fl2va - ref2va.json"' in text
    assert 'MOTION_CONTEXT_TEMPLATE_ALIAS="h3_motion_context_smoke"' in text
    assert 'MOTION_CONTEXT_WORKFLOW_TOOL_REV="e2c7049212dcfd0673e39d35ba19aaa203b13e6e"' in text
    assert 'h3_studio_install_pinned_checkout "$MOTION_CONTEXT_NODE_NAME" "$MOTION_CONTEXT_NODE_REPO" "$MOTION_CONTEXT_NODE_REV" "$motion_target"' in text
    assert 'h3_studio_install_motion_context_template' in text
    assert 'h3_motion_context_workflow.py' in text
    assert 'h3_profile_install_requirements "$motion_target"' in text
    assert text.index("  h3_studio_install_runtime_nodes\n") < text.index("  h3_studio_try_install_motion_context\n") < text.index("  h3_profile_finish\n")
    assert "h3_motion_context_smoke.py" not in text


def test_motion_context_failures_warn_and_leave_t8_template_path_running():
    text = (ROOT / STUDIO_SCRIPT).read_text()
    runtime = text.split("h3_studio_install_runtime_nodes() {", 1)[1].split("\n}\n", 1)[0]
    assert "MOTION_CONTEXT_NODE_NAME" not in runtime
    checkout = text.split("h3_studio_install_pinned_checkout() {", 1)[1].split("\n}\n", 1)[0]
    assert 'git clone --filter=blob:none "$repo" "$target" || return 1' in checkout
    assert 'git -C "$target" fetch --force --depth=1 origin "$revision" || return 1' in checkout
    assert 'git -C "$target" checkout --detach --force "$revision" || return 1' in checkout
    assert text.index("  h3_studio_install_runtime_nodes\n") < text.index("  h3_studio_try_install_motion_context\n") < text.index("  h3_studio_install_t8_long_video_template\n")
    function = "h3_studio_try_install_motion_context() {" + text.split(
        "h3_studio_try_install_motion_context() {", 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    for failing_step in ("checkout", "requirements", "template"):
        harness = f"""set -Eeuo pipefail
FAIL_AT={failing_step}
COMFY_DIR=/tmp/comfy
MOTION_CONTEXT_NODE_NAME=ComfyUI-H3-Motion-Context
MOTION_CONTEXT_NODE_REPO=unused
MOTION_CONTEXT_NODE_REV=unused
h3_profile_warn() {{ printf '[WARN] %s\\n' "$*" >&2; }}
h3_studio_install_pinned_checkout() {{ [[ "$FAIL_AT" != checkout ]]; }}
h3_profile_install_requirements() {{ [[ "$FAIL_AT" != requirements ]]; }}
h3_studio_install_motion_context_template() {{ [[ "$FAIL_AT" != template ]]; }}
h3_studio_install_t8_long_video_template() {{ printf 'T8_READY\\n'; }}
{function}
h3_studio_try_install_motion_context
h3_studio_install_t8_long_video_template
"""
        run = subprocess.run([BASH, "-c", harness], capture_output=True, text=True)
        assert run.returncode == 0, run.stderr
        assert "T8_READY" in run.stdout
        assert "[WARN]" in run.stderr and "Motion Context" in run.stderr


def test_h3_profile_scripts_are_flat_and_parse():
    for rel in SCRIPTS:
        path = ROOT / rel
        assert path.is_file(), rel
        subprocess.run([BASH, "-n", str(path)], check=True)


def test_no_experimental_script_directory():
    assert not (ROOT / "scripts" / "experimental").exists()


def test_legacy_fast_script_is_removed():
    assert not (ROOT / "scripts" / "setupp_h3_comfui_fast.sh").exists()


def test_deployment_profile_registry_points_to_real_scripts():
    registry = json.loads((ROOT / "config" / "deployment_profiles.json").read_text())
    ids = set()
    for profile in registry["profiles"]:
        assert profile["id"] not in ids
        ids.add(profile["id"])
        assert profile["id"] != "legacy-fast"
        assert (ROOT / profile["script"]).is_file(), profile["script"]


def test_profile_scripts_can_load_common_helper_before_branch_merge():
    for rel in PROFILE_SCRIPTS:
        text = (ROOT / rel).read_text()
        assert "H3_PROFILE_COMMON_URL" in text, rel
        assert "vast_setup_script/main/scripts/h3_profile_common.sh" in text, rel
        assert "vast_setup_script/refactor-h3-deploy-scripts/scripts/h3_profile_common.sh" in text, rel


def test_cache_profile_owns_cache_implementation():
    text = (ROOT / "scripts" / "setupp_h3_comfui_cache.sh").read_text()
    assert "setupp_h3_comfui_fast.sh" not in text
    assert "h3_profile_common.sh" in text
    assert "ComfyUI-Spectrum-MiniMax-H3" in text
    assert "ComfyUI-MiniMaxH3-FirstBlockCache" in text


def test_t8_prompt_enhancer_is_installed_only_by_native_profile():
    native = (ROOT / NATIVE_SCRIPT).read_text()
    assert 'H3_MIN_COMFYUI_VERSION="0.33.0"' in native
    install = native.index("h3_profile_install_pinned_node")
    finish = native.index("h3_profile_finish")
    verify = native.index("h3_profile_verify_t8_prompt_enhancer")
    assert install < finish < verify

    for rel in [*PROFILE_SCRIPTS, STUDIO_SCRIPT]:
        text = (ROOT / rel).read_text()
        assert "comfyui-minimax-h3-prompt-enhancer-T8.git" not in text, rel


def test_t8_blockcache_is_installed_only_by_native_profile():
    native = (ROOT / NATIVE_SCRIPT).read_text()
    assert 'T8_BLOCKCACHE_REV="36336dcee1ecb49a5ee98426456aeb353c8535bd"' in native
    assert 'h3_profile_install_pinned_node "$T8_BLOCKCACHE_NODE" "$T8_BLOCKCACHE_REPO" "$T8_BLOCKCACHE_REV"' in native
    assert "h3_profile_verify_t8_blockcache" in native

    for rel in [*PROFILE_SCRIPTS, STUDIO_SCRIPT]:
        text = (ROOT / rel).read_text()
        assert "comfyui-minimax-h3-blockcache-T8.git" not in text, rel


def test_open_source_studio_profile_is_pinned_and_health_checked():
    text = (ROOT / STUDIO_SCRIPT).read_text()
    assert 'H3_SETUP_SUPPORT_REV="${H3_SETUP_SUPPORT_REV:-708bed8034d80515a9df63e7a1f46bca96188cca}"' in text
    assert '$H3_SETUP_SUPPORT_REV/scripts/h3_profile_common.sh' in text
    assert '$H3_SETUP_SUPPORT_REV/scripts/h3_comfui_base.sh' in text
    assert '$H3_SETUP_SUPPORT_REV/scripts/h3_t8_long_video_smoke.py' in text
    assert '$H3_SETUP_SUPPORT_REV/scripts/h3_t8_smoke_job.py' in text
    assert "vast_setup_script/main/scripts/h3_profile_common.sh" not in text
    assert "vast_setup_script/main/scripts/h3_t8_long_video_smoke.py" not in text
    assert "vast_setup_script/main/scripts/h3_t8_smoke_job.py" not in text
    assert "AntaresAlice/h3-webui.git" in text
    assert "9a7206e502f876396d3ad8a61fab7cf3152ad5f5" in text
    assert "kijai/ComfyUI-KJNodes.git" in text
    assert "d3cfe21625e5170126ce06fbfcfe1d88108688c3" in text
    assert "T8mars/comfyui-minimax-h3-audio-T8.git" in text
    assert "2657a6ddf4143998be16d55d24fb03ac0cc5a794" in text
    assert 'T8_LONG_VIDEO_TEMPLATE_ALIAS="h3_t8_long_video_relay"' in text
    assert 'T8_LONG_VIDEO_SMOKE_TEMPLATE_ALIAS="h3_t8_long_video_relay_smoke"' in text
    assert '"h3_t8_relay_smoke_8s"' in text
    assert 'runner_values[1] = 8.0' in text
    assert 'runner_values[2] = 512' in text
    assert 'runner_values[3] = 288' in text
    assert 'relay_values[2] = 192' in text
    assert (ROOT / "scripts" / "h3_t8_long_video_smoke.py").is_file()
    assert (ROOT / "scripts" / "h3_t8_smoke_job.py").is_file()
    assert "h3_studio_install_t8_release_smoke_tool" in text
    assert "H3_T8_SMOKE_JOB_TOOL_URL" in text
    assert "H3_T8_SMOKE_JOB_BIN" in text
    assert 'job_root="$COMFY_DIR/.h3-studio/t8-smoke-jobs"' in text
    assert "--job-root $quoted_job_root" in text
    assert "h3-t8-smoke-job" in text
    assert "h3-t8-long-video-smoke --execute" in text
    assert "H3_T8_SMOKE_TOOL_URL" in text
    assert "2026-08-27_H3_In_Node_Long_Video_Prompt_Relay_EAV_Stock20_Advanced_EXP.json" in text
    assert "h3_studio_install_t8_long_video_template" in text
    assert "h3_studio_verify_t8_long_video_template" in text
    assert "MiniMaxH3LongVideoInNodeLoopEffectsT8Advanced" in text
    assert "Songssx/ComfyUI-MiniMaxH3-TimelineDirector.git" in text
    assert "309b626973d049b073e93557ff94603efc2d1272" in text
    assert "MiniMaxH3全功能合一完全体导演台工作流" in text
    assert 'TIMELINE_TEMPLATE_ALIAS="h3_timeline_director"' in text
    assert 'H3_TIMELINE_MODEL_VARIANT="${H3_TIMELINE_MODEL_VARIANT:-fused}"' in text
    assert 'TIMELINE_NATIVE_UNET_NAME="minimax_h3_ref2va_pruned_int8_convrot.safetensors"' in text
    assert 'TIMELINE_NATIVE_STEPS="20"' in text
    assert 'TIMELINE_FUSED_MODEL_REPO="MATLOWAI/minimax-h3-fused-turbo-int8-convrot"' in text
    assert 'TIMELINE_FUSED_MODEL_REV="3b51096a1bf67608d98131116558202208fcf195"' in text
    assert 'TIMELINE_FUSED_MODEL_SIZE_BYTES="20980178976"' in text
    assert 'TIMELINE_FUSED_MODEL_SHA256="4262e4e9963c553fa00016bbe83961407a4fc0a888be95fd836c8d4f2304e48b"' in text
    assert 'TIMELINE_FUSED_STEPS="8"' in text
    assert 'TIMELINE_CLIP_NAME="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"' in text
    assert 'cp -f "$source_template" "$alias_template"' in text
    assert ".h3_timeline_download" in text
    assert "hf_hub_download" in text
    assert 'named["steps"] = steps' in text
    assert "H3_TIMELINE_MODEL_VARIANT must be fused or native" in text
    assert "MiniMaxH3TimelinePlanner" in text
    assert "MiniMaxH3FiniteSegmentSampler" in text
    assert "MiniMaxH3TimelineSelfLiftSampler" in text
    assert 'H3_STUDIO_PORT="${H3_STUDIO_PORT:-18080}"' in text
    assert "/api/comfyui/status" in text
    assert "/workflow_templates" in text
    assert "h3_studio_verify_webui_node_contract" in text
    assert "MiniMaxH3AudioConditioningT8" in text
    assert "MiniMaxH3DualClockSamplerT8" in text
    assert "MiniMaxH3AVDecodeT8" in text
    assert "MiniMaxH3ReferenceToVideo" in text
    assert "VHS_VideoCombine" in text
    assert "MiniMaxH3DirectorProjectT8" in text
    assert "/minimax_h3_t8/director/ui" in text
    assert "/minimax_h3_t8/director/capabilities" in text
    assert 'required_capabilities = ("long_video", "prompt_relay")' in text

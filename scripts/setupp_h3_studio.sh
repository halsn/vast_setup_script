#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P || true)"
COMMON="${SCRIPT_DIR:+$SCRIPT_DIR/h3_profile_common.sh}"
COMMON_TMP=""
if [[ ! -f "$COMMON" ]]; then
  COMMON_TMP="$(mktemp)"
  COMMON="$COMMON_TMP"
  if [[ -n "${H3_PROFILE_COMMON_URL:-}" ]]; then
    curl -fsSL --retry 3 --connect-timeout 15 "$H3_PROFILE_COMMON_URL" -o "$COMMON"
  else
    curl -fsSL --retry 3 --connect-timeout 15 \
      "https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/h3_profile_common.sh" \
      -o "$COMMON"
  fi
fi
# shellcheck disable=SC1090
source "$COMMON"

H3_STUDIO_REPO="${H3_STUDIO_REPO:-https://github.com/AntaresAlice/h3-webui.git}"
H3_STUDIO_REV="${H3_STUDIO_REV:-9a7206e502f876396d3ad8a61fab7cf3152ad5f5}"
H3_STUDIO_PORT="${H3_STUDIO_PORT:-18080}"
H3_STUDIO_SERVICE_NAME="${H3_STUDIO_SERVICE_NAME:-h3-studio}"
H3_STUDIO_DIR="${H3_STUDIO_DIR:-}"
H3_STUDIO_SUPERVISOR_CONFIG="${H3_STUDIO_SUPERVISOR_CONFIG:-/etc/supervisor/conf.d/h3-studio.conf}"

KJ_NODE_NAME="ComfyUI-KJNodes"
KJ_NODE_REPO="https://github.com/kijai/ComfyUI-KJNodes.git"
KJ_NODE_REV="d3cfe21625e5170126ce06fbfcfe1d88108688c3"
T8_NODE_NAME="comfyui-minimax-h3-audio-T8"
T8_NODE_REPO="https://github.com/T8mars/comfyui-minimax-h3-audio-T8.git"
T8_NODE_REV="2657a6ddf4143998be16d55d24fb03ac0cc5a794"
T8_TEMPLATE_SOURCE_NAME="$T8_NODE_NAME"
T8_LONG_VIDEO_TEMPLATE_SOURCE="examples/workflows/04-long-video/2026-08-27_H3_In_Node_Long_Video_Prompt_Relay_EAV_Stock20_Advanced_EXP.json"
T8_LONG_VIDEO_TEMPLATE_ALIAS="h3_t8_long_video_relay"
T8_LONG_VIDEO_SMOKE_TEMPLATE_ALIAS="h3_t8_long_video_relay_smoke"
T8_LONG_VIDEO_UNET_NAME="minimax_h3_fl2va_pruned_int8_convrot.safetensors"
T8_LONG_VIDEO_CLIP_NAME="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
H3_T8_SMOKE_TOOL_URL="${H3_T8_SMOKE_TOOL_URL:-https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/h3_t8_long_video_smoke.py}"
H3_T8_SMOKE_JOB_TOOL_URL="${H3_T8_SMOKE_JOB_TOOL_URL:-https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/h3_t8_smoke_job.py}"
H3_T8_SMOKE_TOOL_DIR="${H3_T8_SMOKE_TOOL_DIR:-/opt/h3-studio-tools}"
H3_T8_SMOKE_BIN="${H3_T8_SMOKE_BIN:-/usr/local/bin/h3-t8-long-video-smoke}"
H3_T8_SMOKE_JOB_BIN="${H3_T8_SMOKE_JOB_BIN:-/usr/local/bin/h3-t8-smoke-job}"

TIMELINE_NODE_NAME="ComfyUI-MiniMaxH3-TimelineDirector"
TIMELINE_NODE_REPO="https://github.com/Songssx/ComfyUI-MiniMaxH3-TimelineDirector.git"
TIMELINE_NODE_REV="309b626973d049b073e93557ff94603efc2d1272"
TIMELINE_TEMPLATE_SOURCE_NAME="MiniMaxH3全功能合一完全体导演台工作流"
TIMELINE_TEMPLATE_ALIAS="h3_timeline_director"
TIMELINE_SOURCE_UNET_NAME="minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors"
TIMELINE_SOURCE_CLIP_NAME='minimax_h3\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors'
TIMELINE_NATIVE_UNET_NAME="minimax_h3_ref2va_pruned_int8_convrot.safetensors"
TIMELINE_NATIVE_STEPS="20"
TIMELINE_FUSED_MODEL_REPO="MATLOWAI/minimax-h3-fused-turbo-int8-convrot"
TIMELINE_FUSED_MODEL_REV="3b51096a1bf67608d98131116558202208fcf195"
TIMELINE_FUSED_MODEL_FILE="diffusion_models/minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors"
TIMELINE_FUSED_MODEL_NAME="${TIMELINE_FUSED_MODEL_FILE##*/}"
TIMELINE_FUSED_MODEL_SIZE_BYTES="20980178976"
TIMELINE_FUSED_MODEL_SHA256="4262e4e9963c553fa00016bbe83961407a4fc0a888be95fd836c8d4f2304e48b"
TIMELINE_FUSED_STEPS="8"
TIMELINE_CLIP_NAME="qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
H3_TIMELINE_MODEL_VARIANT="${H3_TIMELINE_MODEL_VARIANT:-fused}"
TIMELINE_UNET_NAME=""
TIMELINE_DEFAULT_STEPS=""
H3_INSTALL_TIMELINE_DIRECTOR="${H3_INSTALL_TIMELINE_DIRECTOR:-1}"

cleanup_studio_profile() {
  h3_profile_cleanup || true
  if [[ -n "${COMMON_TMP:-}" && -f "$COMMON_TMP" ]]; then
    rm -f "$COMMON_TMP"
  fi
}
trap cleanup_studio_profile EXIT

h3_studio_install_pinned_checkout() {
  local name="$1" repo="$2" revision="$3" target="$4" actual_rev
  if [[ -e "$target" && ! -d "$target/.git" ]]; then
    h3_profile_error "Existing $name path is not a git checkout: $target"
    return 1
  fi
  if [[ ! -d "$target/.git" ]]; then
    git clone --filter=blob:none "$repo" "$target"
  fi
  git -C "$target" fetch --force --depth=1 origin "$revision"
  git -C "$target" checkout --detach --force "$revision"
  actual_rev="$(git -C "$target" rev-parse HEAD)"
  [[ "$actual_rev" == "$revision" ]] \
    || { h3_profile_error "$name revision mismatch: $actual_rev"; return 1; }
}

h3_studio_install_runtime_nodes() {
  local kj_target="$COMFY_DIR/custom_nodes/$KJ_NODE_NAME"
  local t8_target="$COMFY_DIR/custom_nodes/$T8_NODE_NAME"
  mkdir -p "$COMFY_DIR/custom_nodes"

  # The Studio enables MiniMaxChunkFeedForward/MiniMaxLowVRAMAttention by
  # default, so pin KJNodes to a revision known to expose those nodes.
  h3_studio_install_pinned_checkout "$KJ_NODE_NAME" "$KJ_NODE_REPO" "$KJ_NODE_REV" "$kj_target"
  h3_profile_install_requirements "$kj_target"

  h3_studio_install_pinned_checkout "$T8_NODE_NAME" "$T8_NODE_REPO" "$T8_NODE_REV" "$t8_target"
  h3_profile_install_requirements "$t8_target"
  h3_profile_info "Pinned KJNodes + T8 H3 runtime nodes installed for the open-source Studio."
}

h3_studio_install_t8_long_video_template() {
  local target="$COMFY_DIR/custom_nodes/$T8_NODE_NAME"
  local source_template="$target/$T8_LONG_VIDEO_TEMPLATE_SOURCE"
  local template_dir="$target/example_workflows"
  local standard_template="$template_dir/$T8_LONG_VIDEO_TEMPLATE_ALIAS.json"
  local smoke_template="$template_dir/$T8_LONG_VIDEO_SMOKE_TEMPLATE_ALIAS.json"

  [[ -f "$source_template" ]] || {
    h3_profile_error "T8 Long Video + Prompt Relay source workflow is missing: $source_template"
    return 1
  }
  mkdir -p "$template_dir"
  cp -f "$source_template" "$standard_template"
  cp -f "$source_template" "$smoke_template"

  "$COMFY_PYTHON" - \
    "$standard_template" \
    "$smoke_template" \
    "$T8_LONG_VIDEO_UNET_NAME" \
    "$T8_LONG_VIDEO_CLIP_NAME" <<'PY'
import json
import os
import sys
import tempfile

standard_path, smoke_path, unet_name, clip_name = sys.argv[1:]


def load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def nodes_by_type(workflow):
    return {
        str(node.get("type")): node
        for node in workflow.get("nodes", [])
        if isinstance(node, dict)
    }


def validate_base(workflow, label):
    by_type = nodes_by_type(workflow)
    required = {
        "MiniMaxH3PromptRelayPlanT8Advanced",
        "MiniMaxH3LongVideoInNodeLoopEffectsT8Advanced",
    }
    missing = sorted(required - set(by_type))
    if missing:
        raise SystemExit(
            f"{label} is missing required T8 nodes: " + ", ".join(missing)
        )
    serialized = json.dumps(workflow, ensure_ascii=False)
    for expected in (unet_name, clip_name):
        if expected not in serialized:
            raise SystemExit(
                f"{label} does not reference installed model: {expected}"
            )
    return by_type


standard = load(standard_path)
standard_nodes = validate_base(standard, "T8 standard long-video alias")

# Build a cheap but meaningful release preset from the pinned upstream graph:
# 8.0s @ 24fps = 192 final frames. With a 124-frame render window and 22-frame
# context this crosses one seam (124 + 68 new frames), so it exercises long-video
# continuation/Relay while avoiding the 30s demo's cost.
smoke = load(smoke_path)
smoke_nodes = validate_base(smoke, "T8 smoke long-video alias")
relay = smoke_nodes["MiniMaxH3PromptRelayPlanT8Advanced"]
runner = smoke_nodes["MiniMaxH3LongVideoInNodeLoopEffectsT8Advanced"]
relay_values = relay.get("widgets_values")
runner_values = runner.get("widgets_values")
if not isinstance(relay_values, list) or len(relay_values) < 3:
    raise SystemExit("T8 smoke alias Relay widget contract changed")
if not isinstance(runner_values, list) or len(runner_values) < 43:
    raise SystemExit("T8 smoke alias long-video widget contract changed")

expected_source = {
    "relay_frames": 720,
    "chain_id": "h3_in_node_relay_eav_stock20_demo",
    "duration": 30.0,
    "width": 736,
    "height": 416,
    "render_window": 124,
    "context": 22,
    "relay_mode": "apply_exp",
    "eav_mode": "apply_exp",
    "steps": 20,
}
actual_source = {
    "relay_frames": relay_values[2],
    "chain_id": runner_values[0],
    "duration": runner_values[1],
    "width": runner_values[2],
    "height": runner_values[3],
    "render_window": runner_values[4],
    "context": runner_values[5],
    "relay_mode": runner_values[8],
    "eav_mode": runner_values[10],
    "steps": runner_values[19],
}
if actual_source != expected_source:
    raise SystemExit(
        "Pinned T8 source workflow widget contract changed; refusing to build smoke preset: "
        + repr(actual_source)
    )

relay_values[2] = 192
runner_values[0] = "h3_t8_relay_smoke_8s"
runner_values[1] = 8.0
runner_values[2] = 512
runner_values[3] = 288
runner_values[37] = "H3_T8_Relay_Smoke_8s"
relay["title"] = "5. Global Prompt Relay · 8s / 192-frame validation timeline"
runner["title"] = "6. Validation preset · 8s 512x288 · 2 segments · Stock20 + Relay + EAV"

for node in smoke.get("nodes", []):
    if node.get("type") != "MarkdownNote":
        continue
    title = str(node.get("title", ""))
    if title.startswith("NOTE 1"):
        node["widgets_values"] = [
            "## 8秒低成本验证预设\n"
            "192帧@24fps；固定124帧窗口 + 22帧上下文会生成2段（124 + 68新帧），"
            "因此仍会真实经过一次长视频接缝。Prompt Relay全局Plan长度已同步为192帧。"
            "验证通过后再切换30秒标准模板。"
        ]
        break

with tempfile.NamedTemporaryFile(
    "w", encoding="utf-8", dir=os.path.dirname(smoke_path), delete=False
) as handle:
    json.dump(smoke, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
    temporary = handle.name
os.replace(temporary, smoke_path)

# Re-read the generated preset to make the release contract explicit.
smoke_check = load(smoke_path)
check_nodes = validate_base(smoke_check, "generated T8 smoke preset")
relay_check = check_nodes["MiniMaxH3PromptRelayPlanT8Advanced"]["widgets_values"]
runner_check = check_nodes["MiniMaxH3LongVideoInNodeLoopEffectsT8Advanced"]["widgets_values"]
expected_smoke = {
    "relay_frames": 192,
    "chain_id": "h3_t8_relay_smoke_8s",
    "duration": 8.0,
    "width": 512,
    "height": 288,
    "render_window": 124,
    "context": 22,
    "relay_mode": "apply_exp",
    "eav_mode": "apply_exp",
    "steps": 20,
}
actual_smoke = {
    "relay_frames": relay_check[2],
    "chain_id": runner_check[0],
    "duration": runner_check[1],
    "width": runner_check[2],
    "height": runner_check[3],
    "render_window": runner_check[4],
    "context": runner_check[5],
    "relay_mode": runner_check[8],
    "eav_mode": runner_check[10],
    "steps": runner_check[19],
}
if actual_smoke != expected_smoke:
    raise SystemExit("Generated T8 smoke preset contract mismatch: " + repr(actual_smoke))
PY

  h3_profile_info "T8 standard template installed: $T8_LONG_VIDEO_TEMPLATE_ALIAS (30s 736x416 Stock20)."
  h3_profile_info "T8 validation template installed: $T8_LONG_VIDEO_SMOKE_TEMPLATE_ALIAS (8s 512x288, 2 segments)."
}
h3_studio_install_t8_release_smoke_tool() {
  local tool_path="$H3_T8_SMOKE_TOOL_DIR/h3_t8_long_video_smoke.py"
  local job_tool_path="$H3_T8_SMOKE_TOOL_DIR/h3_t8_smoke_job.py"
  local source_path="${SCRIPT_DIR:+$SCRIPT_DIR/h3_t8_long_video_smoke.py}"
  local job_source_path="${SCRIPT_DIR:+$SCRIPT_DIR/h3_t8_smoke_job.py}"
  local quoted_python quoted_tool quoted_job_tool quoted_root quoted_url quoted_smoke_bin

  mkdir -p "$H3_T8_SMOKE_TOOL_DIR" "$(dirname "$H3_T8_SMOKE_BIN")" "$(dirname "$H3_T8_SMOKE_JOB_BIN")"
  if [[ -n "$source_path" && -f "$source_path" ]]; then
    cp -f "$source_path" "$tool_path"
  else
    curl -fsSL --retry 3 --connect-timeout 15 "$H3_T8_SMOKE_TOOL_URL" -o "$tool_path"
  fi
  if [[ -n "$job_source_path" && -f "$job_source_path" ]]; then
    cp -f "$job_source_path" "$job_tool_path"
  else
    curl -fsSL --retry 3 --connect-timeout 15 "$H3_T8_SMOKE_JOB_TOOL_URL" -o "$job_tool_path"
  fi
  chmod 0755 "$tool_path" "$job_tool_path"
  "$COMFY_PYTHON" -m py_compile "$tool_path" "$job_tool_path"

  printf -v quoted_python '%q' "$COMFY_PYTHON"
  printf -v quoted_tool '%q' "$tool_path"
  printf -v quoted_job_tool '%q' "$job_tool_path"
  printf -v quoted_root '%q' "$COMFY_DIR"
  printf -v quoted_url '%q' "http://127.0.0.1:$COMFY_PORT"
  printf -v quoted_smoke_bin '%q' "$H3_T8_SMOKE_BIN"
  cat > "$H3_T8_SMOKE_BIN" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec $quoted_python $quoted_tool --comfy-root $quoted_root --comfy-url $quoted_url "\$@"
EOF
  cat > "$H3_T8_SMOKE_JOB_BIN" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export H3_T8_SMOKE_BIN=$quoted_smoke_bin
exec $quoted_python $quoted_job_tool "\$@"
EOF
  chmod 0755 "$H3_T8_SMOKE_BIN" "$H3_T8_SMOKE_JOB_BIN"
  "$H3_T8_SMOKE_JOB_BIN" --help >/dev/null

  h3_profile_info "Installed T8 GPU release smoke command: $H3_T8_SMOKE_BIN"
  h3_profile_info "Installed durable T8 smoke job command: $H3_T8_SMOKE_JOB_BIN"
  h3_profile_info "Preflight: h3-t8-long-video-smoke ; paid interrupt/resume run: h3-t8-long-video-smoke --execute"
}
h3_studio_configure_timeline_model() {
  case "$H3_TIMELINE_MODEL_VARIANT" in
    fused)
      TIMELINE_UNET_NAME="$TIMELINE_FUSED_MODEL_NAME"
      TIMELINE_DEFAULT_STEPS="$TIMELINE_FUSED_STEPS"
      ;;
    native)
      TIMELINE_UNET_NAME="$TIMELINE_NATIVE_UNET_NAME"
      TIMELINE_DEFAULT_STEPS="$TIMELINE_NATIVE_STEPS"
      ;;
    *)
      h3_profile_error "H3_TIMELINE_MODEL_VARIANT must be fused or native."
      return 1
      ;;
  esac
  h3_profile_info "Timeline Director model variant: $H3_TIMELINE_MODEL_VARIANT ($TIMELINE_UNET_NAME, $TIMELINE_DEFAULT_STEPS steps)."
}

h3_studio_install_timeline_director() {
  if [[ "$H3_INSTALL_TIMELINE_DIRECTOR" != "1" ]]; then
    h3_profile_info "Timeline Director disabled by H3_INSTALL_TIMELINE_DIRECTOR=$H3_INSTALL_TIMELINE_DIRECTOR"
    return 0
  fi

  local target="$COMFY_DIR/custom_nodes/$TIMELINE_NODE_NAME"
  local source_template="$target/example_workflows/$TIMELINE_TEMPLATE_SOURCE_NAME.json"
  local alias_template="$target/example_workflows/$TIMELINE_TEMPLATE_ALIAS.json"
  mkdir -p "$COMFY_DIR/custom_nodes"
  h3_studio_install_pinned_checkout \
    "$TIMELINE_NODE_NAME" \
    "$TIMELINE_NODE_REPO" \
    "$TIMELINE_NODE_REV" \
    "$target"
  h3_profile_install_requirements "$target"

  [[ -f "$source_template" ]] || {
    h3_profile_error "Timeline Director source template is missing: $source_template"
    return 1
  }
  cp -f "$source_template" "$alias_template"

  # The pinned upstream example uses a creator-specific fused Turbo checkpoint
  # and a CLIP subdirectory name that are not part of our shared H3 model
  # manifest. Rewrite only the URL alias to the models this profile actually
  # installs, keeping the upstream source workflow untouched.
  "$COMFY_PYTHON" - \
    "$alias_template" \
    "$TIMELINE_UNET_NAME" \
    "$TIMELINE_CLIP_NAME" \
    "$TIMELINE_DEFAULT_STEPS" <<'PY'
import json
import os
import sys
import tempfile

path, unet_name, clip_name, steps_text = sys.argv[1:]
steps = int(steps_text)

with open(path, encoding="utf-8") as handle:
    workflow = json.load(handle)

replacements = {
    "minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors": unet_name,
    r"minimax_h3\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors": clip_name,
}

def rewrite(value):
    if isinstance(value, dict):
        return {key: rewrite(child) for key, child in value.items()}
    if isinstance(value, list):
        return [rewrite(child) for child in value]
    if isinstance(value, str):
        return replacements.get(value, value)
    return value

workflow = rewrite(workflow)

scheduler_found = False
for node in workflow.get("nodes", []):
    if node.get("type") != "BasicScheduler":
        continue
    named = node.get("widgets_values_named")
    if isinstance(named, dict):
        named["steps"] = steps
    values = node.get("widgets_values")
    if isinstance(values, list) and len(values) >= 2:
        values[1] = steps
    scheduler_found = True

if not scheduler_found:
    raise SystemExit("Timeline Director alias is missing BasicScheduler")

serialized = json.dumps(workflow, ensure_ascii=False)
for old, new in replacements.items():
    if old != new and old in serialized:
        raise SystemExit(f"Timeline Director alias still references unsupported model: {old}")
for expected in (unet_name, clip_name):
    if expected not in serialized:
        raise SystemExit(f"Timeline Director alias does not reference installed model: {expected}")

with tempfile.NamedTemporaryFile(
    "w", encoding="utf-8", dir=os.path.dirname(path), delete=False
) as handle:
    json.dump(workflow, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
    temporary = handle.name
os.replace(temporary, path)
PY

  h3_profile_info "Timeline Director URL template alias installed: $TIMELINE_TEMPLATE_ALIAS"
  h3_profile_info "Timeline Director alias uses $TIMELINE_UNET_NAME + $TIMELINE_CLIP_NAME with $TIMELINE_DEFAULT_STEPS steps."
  h3_profile_info "Pinned MiniMax H3 Timeline Director installed at $TIMELINE_NODE_REV."
}

h3_studio_install_timeline_model() {
  if [[ "$H3_INSTALL_TIMELINE_DIRECTOR" != "1" || "$H3_TIMELINE_MODEL_VARIANT" != "fused" ]]; then
    return 0
  fi

  local target_dir="$COMFY_DIR/models/diffusion_models"
  mkdir -p "$target_dir"
  h3_profile_ensure_hf

  "$COMFY_PYTHON" - \
    "$TIMELINE_FUSED_MODEL_REPO" \
    "$TIMELINE_FUSED_MODEL_FILE" \
    "$TIMELINE_FUSED_MODEL_REV" \
    "$TIMELINE_FUSED_MODEL_SHA256" \
    "$TIMELINE_FUSED_MODEL_SIZE_BYTES" \
    "$target_dir" <<'PY'
import hashlib
import os
import shutil
import sys

from huggingface_hub import hf_hub_download

repo, filename, revision, expected_sha256, expected_size, target_dir = sys.argv[1:]
expected_size = int(expected_size)
os.makedirs(target_dir, exist_ok=True)
dst = os.path.join(target_dir, os.path.basename(filename))
models_root = os.path.dirname(target_dir)
staging_root = os.path.join(models_root, ".h3_timeline_download")

def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

if os.path.isfile(dst):
    actual_size = os.path.getsize(dst)
    actual_sha256 = sha256_file(dst) if actual_size == expected_size else ""
    if actual_size == expected_size and actual_sha256 == expected_sha256:
        print(f"[OK] verified existing Timeline Director checkpoint: {dst}")
        raise SystemExit(0)
    print(
        "[WARN] Timeline Director checkpoint failed size/SHA verification; "
        f"removing invalid file: {dst}",
        file=sys.stderr,
    )
    os.remove(dst)

required_free = expected_size + 2 * 1024**3
available_free = shutil.disk_usage(models_root).free
if available_free < required_free:
    raise SystemExit(
        "Timeline Director fused checkpoint needs at least "
        f"{required_free} bytes free on {models_root}; only {available_free} bytes are available."
    )

shutil.rmtree(staging_root, ignore_errors=True)
os.makedirs(staging_root, exist_ok=True)
try:
    src = hf_hub_download(
        repo_id=repo,
        filename=filename,
        revision=revision,
        local_dir=staging_root,
    )
    actual_size = os.path.getsize(src)
    if actual_size != expected_size:
        raise SystemExit(
            "Downloaded Timeline Director checkpoint size mismatch: "
            f"expected {expected_size}, got {actual_size}"
        )
    actual_sha256 = sha256_file(src)
    if actual_sha256 != expected_sha256:
        raise SystemExit(
            "Downloaded Timeline Director checkpoint failed SHA-256 verification: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )
    os.replace(src, dst)
finally:
    shutil.rmtree(staging_root, ignore_errors=True)

print(f"[OK] installed pinned Timeline Director checkpoint: {dst} @ {revision}")
PY
}

h3_studio_install_webui() {
  if [[ -z "$H3_STUDIO_DIR" ]]; then
    H3_STUDIO_DIR="$(dirname "$COMFY_DIR")/h3-webui"
  fi
  mkdir -p "$(dirname "$H3_STUDIO_DIR")"
  h3_studio_install_pinned_checkout "AntaresAlice/h3-webui" "$H3_STUDIO_REPO" "$H3_STUDIO_REV" "$H3_STUDIO_DIR"
  mkdir -p "$H3_STUDIO_DIR/webui/workspaces"
  h3_profile_info "Open-source H3 Studio pinned at $H3_STUDIO_REV."
}

h3_studio_write_supervisor_config() {
  mkdir -p "$(dirname "$H3_STUDIO_SUPERVISOR_CONFIG")" /var/log
  cat > "$H3_STUDIO_SUPERVISOR_CONFIG" <<EOF
[program:$H3_STUDIO_SERVICE_NAME]
directory=$H3_STUDIO_DIR
command=$COMFY_PYTHON $H3_STUDIO_DIR/webui/server.py
autostart=true
autorestart=true
startsecs=3
startretries=10
stopasgroup=true
killasgroup=true
environment=COMFYUI_URL="http://127.0.0.1:$COMFY_PORT",COMFYUI_INPUT="$COMFY_DIR/input",COMFYUI_OUTPUT="$COMFY_DIR/output",H3WEBUI_HOST="0.0.0.0",H3WEBUI_PORT="$H3_STUDIO_PORT",H3_NO_BROWSER="1"
stdout_logfile=/var/log/h3-studio.log
stdout_logfile_maxbytes=20MB
stdout_logfile_backups=2
stderr_logfile=/var/log/h3-studio.err.log
stderr_logfile_maxbytes=20MB
stderr_logfile_backups=2
EOF
}

h3_studio_start() {
  supervisorctl reread
  supervisorctl update
  if supervisorctl status "$H3_STUDIO_SERVICE_NAME" >/dev/null 2>&1; then
    supervisorctl restart "$H3_STUDIO_SERVICE_NAME"
  else
    supervisorctl start "$H3_STUDIO_SERVICE_NAME"
  fi
}


h3_studio_verify_webui_node_contract() {
  "$COMFY_PYTHON" - "http://127.0.0.1:$COMFY_PORT/object_info" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
required = (
    "VAELoader",
    "CLIPLoader",
    "UNETLoader",
    "LoadImage",
    "LoadAudio",
    "LoadVideo",
    "GetVideoComponents",
    "RandomNoise",
    "BasicGuider",
    "KSamplerSelect",
    "BasicScheduler",
    "SamplerCustomAdvanced",
    "LoraLoaderModelOnly",
    "VAEDecode",
    "VAEDecodeAudio",
    "CreateVideo",
    "SaveVideo",
    "VHS_VideoCombine",
    "MiniMaxChunkFeedForward",
    "MiniMaxLowVRAMAttention",
    "MiniMaxH3MemoryEfficientSageAttentionPatch",
    "MiniMaxH3AudioConditioningT8",
    "MiniMaxH3DualClockSamplerT8",
    "MiniMaxH3AVDecodeT8",
    "MiniMaxH3ReferenceToVideo",
    "MiniMaxH3SigmaShift",
)

try:
    with urllib.request.urlopen(url, timeout=30) as response:
        catalog = json.load(response)
except Exception as exc:
    print(f"[ERROR] Could not read ComfyUI object catalog for H3 Studio: {exc}", file=sys.stderr)
    raise SystemExit(1)

missing = [name for name in required if name not in catalog]
if missing:
    print(
        "[ERROR] H3 Studio required ComfyUI nodes are missing: " + ", ".join(missing),
        file=sys.stderr,
    )
    raise SystemExit(1)

print(
    f"[OK] H3 Studio node contract is ready ({len(required)} required node classes)",
    file=sys.stderr,
)
PY
}

h3_studio_verify_t8_director() {
  "$COMFY_PYTHON" - \
    "http://127.0.0.1:$COMFY_PORT/object_info" \
    "http://127.0.0.1:$COMFY_PORT/minimax_h3_t8/director/ui" \
    "http://127.0.0.1:$COMFY_PORT/minimax_h3_t8/director/capabilities" <<'PY'
import json
import sys
import urllib.request

object_info_url, ui_url, capabilities_url = sys.argv[1:]

try:
    with urllib.request.urlopen(object_info_url, timeout=20) as response:
        catalog = json.load(response)
except Exception as exc:
    print(f"[ERROR] Could not read ComfyUI object catalog for T8 Director: {exc}", file=sys.stderr)
    raise SystemExit(1)

required_node = "MiniMaxH3DirectorProjectT8"
if required_node not in catalog:
    print(f"[ERROR] T8 Obsidian Director node is not registered: {required_node}", file=sys.stderr)
    raise SystemExit(1)

try:
    with urllib.request.urlopen(ui_url, timeout=20) as response:
        html = response.read().decode("utf-8", "replace")
except Exception as exc:
    print(f"[ERROR] Could not load T8 Obsidian Director UI: {exc}", file=sys.stderr)
    raise SystemExit(1)

if "曜石导演台" not in html and "Obsidian" not in html:
    print("[ERROR] T8 Director UI route returned unexpected content.", file=sys.stderr)
    raise SystemExit(1)

try:
    with urllib.request.urlopen(capabilities_url, timeout=20) as response:
        capabilities = json.load(response)
except Exception as exc:
    print(f"[ERROR] Could not read T8 Director capabilities: {exc}", file=sys.stderr)
    raise SystemExit(1)

if capabilities.get("schema") != "t8.minimax_h3.director_capabilities.v1":
    print(
        "[ERROR] T8 Director capabilities schema mismatch: "
        + str(capabilities.get("schema")),
        file=sys.stderr,
    )
    raise SystemExit(1)

rows = capabilities.get("capabilities")
if not isinstance(rows, list) or not rows:
    print("[ERROR] T8 Director capabilities inventory is empty.", file=sys.stderr)
    raise SystemExit(1)

required_capabilities = ("long_video", "prompt_relay")
by_id = {
    row.get("id"): row
    for row in rows
    if isinstance(row, dict) and isinstance(row.get("id"), str)
}
unready = [
    capability
    for capability in required_capabilities
    if by_id.get(capability, {}).get("state") != "ready"
]
if unready:
    print(
        "[ERROR] T8 Studio engine capabilities are not ready: " + ", ".join(unready),
        file=sys.stderr,
    )
    raise SystemExit(1)

print(
    "[OK] T8 Obsidian Director + Long Video + Prompt Relay engine contract is ready",
    file=sys.stderr,
)
PY
}

h3_studio_verify_t8_long_video_template() {
  "$COMFY_PYTHON" - \
    "http://127.0.0.1:$COMFY_PORT/object_info" \
    "http://127.0.0.1:$COMFY_PORT/workflow_templates" \
    "http://127.0.0.1:$COMFY_PORT/api/workflow_templates" \
    "$T8_TEMPLATE_SOURCE_NAME" \
    "$T8_LONG_VIDEO_TEMPLATE_ALIAS" \
    "$T8_LONG_VIDEO_SMOKE_TEMPLATE_ALIAS" \
    "$T8_LONG_VIDEO_UNET_NAME" \
    "$T8_LONG_VIDEO_CLIP_NAME" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

(
    object_info_url,
    templates_url,
    template_base_url,
    source,
    standard_template,
    smoke_template,
    unet_name,
    clip_name,
) = sys.argv[1:]


def read_json(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)


catalog = read_json(object_info_url)
required_nodes = (
    "MiniMaxH3PromptRelayPlanT8Advanced",
    "MiniMaxH3LongVideoInNodeLoopEffectsT8Advanced",
)
missing = [name for name in required_nodes if name not in catalog]
if missing:
    print(
        "[ERROR] T8 Long Video + Prompt Relay template nodes are not registered: "
        + ", ".join(missing),
        file=sys.stderr,
    )
    raise SystemExit(1)

templates = read_json(templates_url)
available = templates.get(source) or []
for template in (standard_template, smoke_template):
    if template not in available:
        print(
            f"[ERROR] T8 engine template '{template}' is not listed for source '{source}'.",
            file=sys.stderr,
        )
        raise SystemExit(1)


def load_template(template):
    template_url = (
        template_base_url.rstrip("/")
        + "/"
        + urllib.parse.quote(source, safe="")
        + "/"
        + urllib.parse.quote(template + ".json", safe="")
    )
    payload = read_json(template_url)
    if not isinstance(payload, dict) or not payload.get("nodes"):
        raise RuntimeError(f"T8 template {template!r} JSON is invalid")
    types = {
        str(node.get("type"))
        for node in payload.get("nodes", [])
        if isinstance(node, dict)
    }
    missing_payload = [name for name in required_nodes if name not in types]
    if missing_payload:
        raise RuntimeError(
            f"T8 template {template!r} is missing nodes: " + ", ".join(missing_payload)
        )
    serialized = json.dumps(payload, ensure_ascii=False)
    for expected in (unet_name, clip_name):
        if expected not in serialized:
            raise RuntimeError(
                f"T8 template {template!r} does not use installed model: {expected}"
            )
    return payload


standard = load_template(standard_template)
smoke = load_template(smoke_template)

by_type = {
    str(node.get("type")): node
    for node in smoke.get("nodes", [])
    if isinstance(node, dict)
}
relay_values = by_type["MiniMaxH3PromptRelayPlanT8Advanced"].get("widgets_values") or []
runner_values = by_type["MiniMaxH3LongVideoInNodeLoopEffectsT8Advanced"].get("widgets_values") or []
actual = {
    "relay_frames": relay_values[2] if len(relay_values) > 2 else None,
    "chain_id": runner_values[0] if len(runner_values) > 0 else None,
    "duration": runner_values[1] if len(runner_values) > 1 else None,
    "width": runner_values[2] if len(runner_values) > 2 else None,
    "height": runner_values[3] if len(runner_values) > 3 else None,
    "render_window": runner_values[4] if len(runner_values) > 4 else None,
    "context": runner_values[5] if len(runner_values) > 5 else None,
    "relay_mode": runner_values[8] if len(runner_values) > 8 else None,
    "eav_mode": runner_values[10] if len(runner_values) > 10 else None,
    "steps": runner_values[19] if len(runner_values) > 19 else None,
}
expected = {
    "relay_frames": 192,
    "chain_id": "h3_t8_relay_smoke_8s",
    "duration": 8.0,
    "width": 512,
    "height": 288,
    "render_window": 124,
    "context": 22,
    "relay_mode": "apply_exp",
    "eav_mode": "apply_exp",
    "steps": 20,
}
if actual != expected:
    print(
        "[ERROR] T8 8s validation preset contract mismatch: " + repr(actual),
        file=sys.stderr,
    )
    raise SystemExit(1)

print(
    "[OK] T8 standard 30s template + 8s two-segment validation preset are ready",
    file=sys.stderr,
)
PY
}
h3_studio_verify_timeline_director() {
  if [[ "$H3_INSTALL_TIMELINE_DIRECTOR" != "1" ]]; then
    return 0
  fi

  "$COMFY_PYTHON" - \
    "http://127.0.0.1:$COMFY_PORT/object_info" \
    "http://127.0.0.1:$COMFY_PORT/workflow_templates" \
    "http://127.0.0.1:$COMFY_PORT/api/workflow_templates" \
    "$TIMELINE_NODE_NAME" \
    "$TIMELINE_TEMPLATE_ALIAS" \
    "$TIMELINE_UNET_NAME" \
    "$TIMELINE_CLIP_NAME" \
    "$TIMELINE_DEFAULT_STEPS" \
    "$TIMELINE_SOURCE_UNET_NAME" <<'PY'
import json
import sys
import urllib.parse
import urllib.request

(
    object_info_url,
    templates_url,
    template_base_url,
    source,
    template,
    unet_name,
    clip_name,
    steps_text,
    source_unet_name,
) = sys.argv[1:]
steps = int(steps_text)

def read_json(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)

try:
    catalog = read_json(object_info_url)
except Exception as exc:
    print(f"[ERROR] Could not read ComfyUI object catalog: {exc}", file=sys.stderr)
    raise SystemExit(1)

required_nodes = (
    "MiniMaxH3TimelinePlanner",
    "MiniMaxH3FiniteSegmentSampler",
    "MiniMaxH3TimelineSelfLiftSampler",
)
missing = [name for name in required_nodes if name not in catalog]
if missing:
    print(
        "[ERROR] Timeline Director nodes are not registered: " + ", ".join(missing),
        file=sys.stderr,
    )
    raise SystemExit(1)

def combo_options(node_name, input_name):
    try:
        value = catalog[node_name]["input"]["required"][input_name][0]
    except (KeyError, IndexError, TypeError):
        return []
    return value if isinstance(value, list) else []

if unet_name not in combo_options("UNETLoader", "unet_name"):
    print(
        f"[ERROR] Timeline Director UNET is not selectable in ComfyUI: {unet_name}",
        file=sys.stderr,
    )
    raise SystemExit(1)
if clip_name not in combo_options("CLIPLoader", "clip_name"):
    print(
        f"[ERROR] Timeline Director CLIP is not selectable in ComfyUI: {clip_name}",
        file=sys.stderr,
    )
    raise SystemExit(1)

try:
    templates = read_json(templates_url)
except Exception as exc:
    print(f"[ERROR] Could not read ComfyUI workflow templates: {exc}", file=sys.stderr)
    raise SystemExit(1)

available = templates.get(source) or []
if template not in available:
    print(
        f"[ERROR] Timeline Director template '{template}' is not listed for source '{source}'.",
        file=sys.stderr,
    )
    raise SystemExit(1)

template_url = (
    template_base_url.rstrip("/")
    + "/"
    + urllib.parse.quote(source, safe="")
    + "/"
    + urllib.parse.quote(template + ".json", safe="")
)
try:
    with urllib.request.urlopen(template_url, timeout=20) as response:
        payload = json.load(response)
except Exception as exc:
    print(f"[ERROR] Could not load Timeline Director template: {exc}", file=sys.stderr)
    raise SystemExit(1)

if not isinstance(payload, dict) or not payload.get("nodes"):
    print("[ERROR] Timeline Director template JSON is invalid.", file=sys.stderr)
    raise SystemExit(1)

serialized = json.dumps(payload, ensure_ascii=False)
unsupported = [r"minimax_h3\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"]
if unet_name != source_unet_name:
    unsupported.append(source_unet_name)
bad = [value for value in unsupported if value in serialized]
if bad:
    print(
        "[ERROR] Timeline Director alias still references unsupported upstream models: "
        + ", ".join(bad),
        file=sys.stderr,
    )
    raise SystemExit(1)
for expected in (unet_name, clip_name):
    if expected not in serialized:
        print(
            f"[ERROR] Timeline Director alias does not use installed model: {expected}",
            file=sys.stderr,
        )
        raise SystemExit(1)

scheduler_steps = [
    (node.get("widgets_values_named") or {}).get("steps")
    for node in payload.get("nodes", [])
    if node.get("type") == "BasicScheduler"
]
if scheduler_steps != [steps]:
    print(
        f"[ERROR] Timeline Director scheduler steps mismatch: {scheduler_steps} != {[steps]}",
        file=sys.stderr,
    )
    raise SystemExit(1)

print(
    "[OK] Timeline Director nodes, installed models, and URL-loadable workflow template are ready",
    file=sys.stderr,
)
PY
}

h3_studio_wait_ready() {
  local attempts=0
  while (( attempts < 60 )); do
    if curl -fsS --max-time 3 "http://127.0.0.1:$H3_STUDIO_PORT/" >/dev/null 2>&1; then
      break
    fi
    sleep 2
    ((attempts+=1))
  done
  (( attempts < 60 )) || {
    supervisorctl tail -100 "$H3_STUDIO_SERVICE_NAME" stderr 2>&1 || true
    h3_profile_error "H3 Studio did not start on port $H3_STUDIO_PORT"
    return 1
  }

  "$COMFY_PYTHON" - "http://127.0.0.1:$H3_STUDIO_PORT/api/comfyui/status" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.load(response)
except Exception as exc:
    print(f"[ERROR] H3 Studio health request failed: {exc}", file=sys.stderr)
    raise SystemExit(1)

if payload.get("up") is not True:
    print(
        "[ERROR] H3 Studio is running but cannot reach ComfyUI: "
        + str(payload.get("error") or payload),
        file=sys.stderr,
    )
    raise SystemExit(1)

print("[OK] H3 Studio and its ComfyUI bridge are ready", file=sys.stderr)
PY
}

main_studio() {
  [[ "$H3_STUDIO_PORT" =~ ^[0-9]+$ ]] && (( H3_STUDIO_PORT >= 1 && H3_STUDIO_PORT <= 65535 )) \
    || { h3_profile_error "H3_STUDIO_PORT must be an integer from 1 to 65535."; return 1; }

  case "${1:-}" in
    -h|--help|--version)
      h3_profile_load_base
      main "$@"
      return
      ;;
    "")
      ;;
    *)
      h3_profile_load_base
      main "$@"
      return
      ;;
  esac

  h3_studio_configure_timeline_model
  h3_profile_prepare_base
  h3_studio_install_runtime_nodes
  h3_studio_install_t8_long_video_template
  h3_studio_install_t8_release_smoke_tool
  h3_studio_install_timeline_director
  h3_studio_install_timeline_model
  h3_studio_install_webui

  # Restart ComfyUI after the Studio-only custom nodes are installed, then
  # verify the advanced Timeline Director contract before starting the WebUI.
  h3_profile_finish
  h3_studio_verify_webui_node_contract
  h3_studio_verify_t8_director
  h3_studio_verify_t8_long_video_template
  h3_studio_verify_timeline_director
  h3_studio_write_supervisor_config
  h3_studio_start
  h3_studio_wait_ready

  h3_profile_info "H3 Studio ready on 0.0.0.0:$H3_STUDIO_PORT (Studio + T8 Long Video/Prompt Relay + T8 Director + Timeline Director, all pinned)."
  h3_profile_info "T8 release smoke: h3-t8-long-video-smoke --execute"
}

main_studio "$@"

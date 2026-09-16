#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P || true)"
COMMON="${SCRIPT_DIR:+$SCRIPT_DIR/h3_profile_common.sh}"
if [[ ! -f "$COMMON" ]]; then
  COMMON="$(mktemp)"
  trap 'rm -f "$COMMON"' EXIT
  if [[ -n "${H3_PROFILE_COMMON_URL:-}" ]]; then
    curl -fsSL --retry 3 --connect-timeout 15 "$H3_PROFILE_COMMON_URL" -o "$COMMON"
  else
    curl -fsSL --retry 3 --connect-timeout 15 \
      "https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/h3_profile_common.sh" \
      -o "$COMMON" \
      || curl -fsSL --retry 3 --connect-timeout 15 \
        "https://raw.githubusercontent.com/halsn/vast_setup_script/refactor-h3-deploy-scripts/scripts/h3_profile_common.sh" \
        -o "$COMMON"
  fi
fi
# shellcheck disable=SC1090
source "$COMMON"

# Current default: FastVideo's official FastH3 8-Step V2 Comfy checkpoint.
# The older community 4-step VSA integration remains available as preview4.
H3_FASTH3_VARIANT="${H3_FASTH3_VARIANT:-v2_8step}"
H3_FASTH3_V2_REPO="${H3_FASTH3_V2_REPO:-FastVideo/FastVideo-FastH3-Comfy}"
H3_FASTH3_V2_MODEL="${H3_FASTH3_V2_MODEL:-diffusion_models/fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors}"
H3_FASTH3_V2_MIN_COMFYUI_VERSION="${H3_FASTH3_V2_MIN_COMFYUI_VERSION:-0.35.0}"
H3_FASTH3_WORKFLOW_BASE_URL="${H3_FASTH3_WORKFLOW_BASE_URL:-https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates}"

# Workbench policy: expose official FastH3 V2 as T2VA first. The Comfy-Org
# FL2VA/I2V template is still installed for inspection, but stays out of the
# direct workbench engine until real GPU validation confirms the distilled
# checkpoint behaves correctly for image-conditioned generation.

# Legacy FastH3 4-step preview compatibility mode.
H3_FASTH3_NODE_REPO="${H3_FASTH3_NODE_REPO:-https://github.com/barelymining/ComfyUI-MiniMax-H3-FastVideo.git}"
H3_FASTH3_MODEL_REPO="${H3_FASTH3_MODEL_REPO:-barelymining/ComfyUI-MiniMax-H3-FastVideo}"
H3_FASTH3_LORA="${H3_FASTH3_LORA:-fasth3_vsa_4-steps-v5.safetensors}"
H3_FASTH3_GATE="${H3_FASTH3_GATE:-fasth3_vsa_gate.safetensors}"
H3_FASTH3_INSTALL_VSA="${H3_FASTH3_INSTALL_VSA:-1}"

validate_fasth3_variant() {
  case "$H3_FASTH3_VARIANT" in
    v2_8step|preview4) ;;
    *)
      h3_profile_error "H3_FASTH3_VARIANT must be v2_8step or preview4 (got '$H3_FASTH3_VARIANT')"
      return 2
      ;;
  esac
}

ensure_fasth3_v2_comfyui_version() {
  local current
  current="$(get_comfyui_version 2>/dev/null || true)"
  if [[ -n "$current" ]] && version_at_least "$current" "$H3_FASTH3_V2_MIN_COMFYUI_VERSION"; then
    log_ok "FastH3 8-Step V2 ComfyUI requirement satisfied: $current"
    return 0
  fi

  if [[ "${H3_SKIP_UPGRADE:-0}" == "1" ]]; then
    die "ComfyUI ${current:-unknown} is below $H3_FASTH3_V2_MIN_COMFYUI_VERSION; official FastH3 V2 VSA-H3 inference requires a newer ComfyUI core."
  fi

  log_warn "FastH3 8-Step V2 requires ComfyUI >= $H3_FASTH3_V2_MIN_COMFYUI_VERSION for native BlockSparseAttention; updating ${current:-unknown}."
  stop_comfyui
  update_git_checkout "$COMFY_DIR" "ComfyUI"
  H3_COMFYUI_CORE_UPDATED=1
  update_python_dependencies
  ensure_comfyui_version

  current="$(get_comfyui_version 2>/dev/null || true)"
  [[ -n "$current" ]] || die "Could not determine ComfyUI version after FastH3 V2 update."
  version_at_least "$current" "$H3_FASTH3_V2_MIN_COMFYUI_VERSION" \
    || die "ComfyUI $current is below $H3_FASTH3_V2_MIN_COMFYUI_VERSION after update."
  log_ok "FastH3 8-Step V2 ComfyUI requirement satisfied after update: $current"
}

install_official_fasth3_workflow() {
  local source_name="$1" target_name="$2"
  local workflow_dir="$COMFY_DIR/custom_nodes/ComfyUI-H3-Worker/example_workflows"
  local target="$workflow_dir/$target_name" tmp
  mkdir -p "$workflow_dir"
  tmp="$(mktemp)"

  if ! curl -fsSL --retry 3 --connect-timeout 15 \
    "$H3_FASTH3_WORKFLOW_BASE_URL/$source_name" -o "$tmp"; then
    rm -f "$tmp"
    h3_profile_error "Could not download official FastH3 workflow: $source_name"
    return 1
  fi
  if ! "$COMFY_PYTHON" - "$tmp" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    json.load(handle)
PY
  then
    rm -f "$tmp"
    h3_profile_error "Official FastH3 workflow is not valid JSON: $source_name"
    return 1
  fi

  mv -f "$tmp" "$target"
  h3_profile_info "Installed official FastH3 workflow: $target_name"
}

install_fasth3_v2() {
  ensure_fasth3_v2_comfyui_version
  h3_profile_hf_file \
    "$H3_FASTH3_V2_REPO" \
    "$H3_FASTH3_V2_MODEL" \
    "$COMFY_DIR/models/diffusion_models"

  install_official_fasth3_workflow \
    "video_fastvideo_fasth3_t2v.json" \
    "H3_FastH3_8Step_V2_T2V.json"
  install_official_fasth3_workflow \
    "video_fastvideo_fasth3_i2v.json" \
    "H3_FastH3_8Step_V2_I2V.json"

  h3_profile_info "FastH3 8-Step V2 installed: official DMD2 checkpoint plus Comfy-Org reference templates."
  h3_profile_info "Workbench FastH3 stays T2VA-only until FL2VA completes real GPU validation."
}

install_vsa_runtime() {
  [[ "$H3_FASTH3_INSTALL_VSA" == "1" ]] || return 0
  if "$COMFY_PYTHON" -c 'import vsa' >/dev/null 2>&1; then
    log_ok "vsa runtime already available."
    return 0
  fi

  "$COMFY_PYTHON" -m pip install "pytest>=8" "vsa==0.0.3" && return 0

  log_warn "Direct vsa install failed (common on non-Hopper GPUs); installing the Python fallback only."
  local tmp
  tmp="$(mktemp -d)"
  "$COMFY_PYTHON" -m pip download --no-deps "vsa==0.0.3" -d "$tmp"
  "$COMFY_PYTHON" - "$tmp" <<'PY'
import glob, os, shutil, site, sys, tarfile, tempfile, zipfile
src_dir = sys.argv[1]
archives = glob.glob(os.path.join(src_dir, "vsa-*"))
if not archives:
    raise SystemExit("vsa archive was not downloaded")
archive = archives[0]
extract = tempfile.mkdtemp(prefix="vsa-extract-")
if archive.endswith((".tar.gz", ".tgz")):
    with tarfile.open(archive) as tf:
        tf.extractall(extract)
elif archive.endswith(".zip"):
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(extract)
else:
    raise SystemExit(f"unsupported vsa archive: {archive}")
matches = glob.glob(os.path.join(extract, "**", "vsa", "__init__.py"), recursive=True)
if not matches:
    raise SystemExit("vsa Python package not found in source archive")
pkg = os.path.dirname(matches[0])
target_root = site.getsitepackages()[0]
target = os.path.join(target_root, "vsa")
if os.path.isdir(target):
    shutil.rmtree(target)
shutil.copytree(pkg, target)
print(f"[OK] installed vsa Python fallback: {target}")
PY
  rm -rf "$tmp"
  "$COMFY_PYTHON" -c 'import vsa; print("[OK] vsa import succeeded")'
}

install_fasth3_preview4() {
  install_vsa_runtime
  h3_profile_install_node "ComfyUI-MiniMax-H3-FastVideo" "$H3_FASTH3_NODE_REPO"

  local dir="$COMFY_DIR/models/loras"
  h3_profile_hf_file "$H3_FASTH3_MODEL_REPO" "$H3_FASTH3_LORA" "$dir"
  h3_profile_hf_file "$H3_FASTH3_MODEL_REPO" "$H3_FASTH3_GATE" "$dir"
  h3_profile_warn "Using legacy FastH3 preview4 compatibility mode (community VSA wrapper, FL2VA-oriented)."
}

verify_fasth3_v2_readiness() {
  [[ "$H3_FASTH3_VARIANT" == "v2_8step" ]] || return 0
  local filename="${H3_FASTH3_V2_MODEL##*/}"
  local object_info_url="http://127.0.0.1:${COMFY_PORT}/object_info"
  "$COMFY_PYTHON" - "$object_info_url" "$filename" <<'PY'
import json
import sys
import urllib.request

url, filename = sys.argv[1:]
try:
    with urllib.request.urlopen(url, timeout=20) as response:
        catalog = json.load(response)
except Exception as exc:
    print(f"[ERROR] Could not read ComfyUI object catalog: {exc}", file=sys.stderr)
    raise SystemExit(1)

visible = set()
def collect(value):
    if isinstance(value, str):
        visible.add(value)
    elif isinstance(value, list):
        for item in value:
            collect(item)
    elif isinstance(value, dict):
        for item in value.values():
            collect(item)

collect(catalog.get("UNETLoader", {}))
if filename not in visible:
    print(f"[ERROR] FastH3 V2 model is not visible to UNETLoader: {filename}", file=sys.stderr)
    raise SystemExit(1)

required_nodes = (
    "MiniMaxH3SigmaShift",
    "ModelAttentionBackend",
    "BlockSparseAttention",
)
missing = [name for name in required_nodes if not catalog.get(name)]
if missing:
    print(f"[ERROR] FastH3 V2 required ComfyUI nodes are missing: {', '.join(missing)}", file=sys.stderr)
    raise SystemExit(1)

sparse_visible = set()
collect_target = sparse_visible
def collect_sparse(value):
    if isinstance(value, str):
        collect_target.add(value)
    elif isinstance(value, list):
        for item in value:
            collect_sparse(item)
    elif isinstance(value, dict):
        for item in value.values():
            collect_sparse(item)
collect_sparse(catalog["BlockSparseAttention"])
if "vsa" not in sparse_visible:
    print("[ERROR] BlockSparseAttention does not expose the VSA selection required by FastH3 V2.", file=sys.stderr)
    raise SystemExit(1)

attention_visible = set()
def collect_attention(value):
    if isinstance(value, str):
        attention_visible.add(value)
    elif isinstance(value, list):
        for item in value:
            collect_attention(item)
    elif isinstance(value, dict):
        for item in value.values():
            collect_attention(item)
collect_attention(catalog["ModelAttentionBackend"])
if "comfy kitchen attention" not in attention_visible:
    print("[ERROR] ModelAttentionBackend does not expose comfy kitchen attention required by FastH3 V2.", file=sys.stderr)
    raise SystemExit(1)

print(f"[OK] FastH3 V2 model registered: {filename}", file=sys.stderr)
print("[OK] FastH3 V2 VSA-H3 runtime nodes registered.", file=sys.stderr)
PY
}

main_fasth3() {
  validate_fasth3_variant
  h3_profile_prepare_base

  case "$H3_FASTH3_VARIANT" in
    v2_8step) install_fasth3_v2 ;;
    preview4) install_fasth3_preview4 ;;
  esac

  h3_profile_finish
  verify_fasth3_v2_readiness

  if [[ "$H3_FASTH3_VARIANT" == "v2_8step" ]]; then
    log_ok "H3 FastH3 profile ready: official FastH3 8-Step V2 with VSA-H3 runtime verified; workbench mode is T2VA."
  else
    log_ok "H3 FastH3 profile ready: legacy preview4 community VSA compatibility mode."
  fi
}

main_fasth3 "$@"

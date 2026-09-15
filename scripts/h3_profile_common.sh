#!/usr/bin/env bash
set -Eeuo pipefail

H3_PROFILE_BASE_URL="${H3_PROFILE_BASE_URL:-https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/h3_comfui_base.sh}"
H3_PROFILE_SCRIPT_SOURCE="${BASH_SOURCE[1]:-${BASH_SOURCE[0]:-}}"
H3_PROFILE_SCRIPT_DIR=""
if [[ -n "${H3_PROFILE_SCRIPT_SOURCE:-}" ]]; then
  H3_PROFILE_SCRIPT_DIR="$(cd "$(dirname "$H3_PROFILE_SCRIPT_SOURCE")" 2>/dev/null && pwd -P || true)"
fi
H3_PROFILE_BASE_TMP=""
VHS_NODE_NAME="ComfyUI-VideoHelperSuite"
VHS_NODE_REPO="https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite.git"
VHS_NODE_REV="4d907bee61e92c2e65af3bd6383a4e4d356126d1"
H3_INSTALL_REFINE="${H3_INSTALL_REFINE:-1}"
REFINE_REQUIRED_FREE_GB="${REFINE_REQUIRED_FREE_GB:-3}"
REFINE_NODE_NAME="Comfyui_Minimax_h3_latent_Upscaler"
REFINE_NODE_REPO="https://github.com/xmarre/Comfyui_Minimax_h3_latent_Upscaler-Plus.git"
REFINE_NODE_REV="620165a311de9b28a36260219fb5cd370a304e3c"
MPI_NODE_NAME="ComfyUi-MpiNodes"
MPI_NODE_REPO="https://github.com/MadPonyInteractive/ComfyUi-MpiNodes.git"
MPI_NODE_REV="1de35a33827b125fe2adbc08df23266c465c032a"
REFINE_MODEL_REPO="LBH-123-AI/Minimax_h3_latent_Upscaler"
REFINE_MODEL_NAME="minimax_h3_latent_upscaler_3d_fp16.safetensors"
REFINE_MODEL_SHA256="043e5a48e161610ef6c3ea974645220354d06fa618abca15f76d084812eb55c2"

h3_profile_info() { printf '[INFO] %s\n' "$*" >&2; }
h3_profile_warn() { printf '[WARN] %s\n' "$*" >&2; }
h3_profile_error() { printf '[ERROR] %s\n' "$*" >&2; return 1; }

h3_profile_cleanup() {
  if [[ -n "${H3_PROFILE_BASE_TMP:-}" && -f "$H3_PROFILE_BASE_TMP" ]]; then
    rm -f "$H3_PROFILE_BASE_TMP"
  fi
}

h3_profile_load_base() {
  local base_script="" local_base="" download_url="$H3_PROFILE_BASE_URL" cachebust
  local_base="${H3_PROFILE_SCRIPT_DIR:+$H3_PROFILE_SCRIPT_DIR/h3_comfui_base.sh}"

  if [[ "${H3_PROFILE_USE_LOCAL_BASE:-0}" == "1" ]]; then
    [[ -n "$local_base" && -f "$local_base" ]] \
      || { h3_profile_error "H3_PROFILE_USE_LOCAL_BASE=1 but local h3_comfui_base.sh was not found."; return 1; }
    base_script="$local_base"
  else
    command -v curl >/dev/null 2>&1 || { h3_profile_error "curl is required."; return 1; }
    H3_PROFILE_BASE_TMP="$(mktemp)"
    cachebust="$(date +%s%N 2>/dev/null || date +%s)"
    if [[ "$download_url" == *\?* ]]; then
      download_url="${download_url}&h3_cachebust=$cachebust"
    else
      download_url="${download_url}?h3_cachebust=$cachebust"
    fi
    h3_profile_info "Fetching stable H3 base core from $H3_PROFILE_BASE_URL"
    curl -fsSL --retry 3 --connect-timeout 15 "$download_url" -o "$H3_PROFILE_BASE_TMP"
    base_script="$H3_PROFILE_BASE_TMP"
  fi

  H3_BOOTSTRAP_LIB_ONLY=1
  # shellcheck disable=SC1090
  source "$base_script"
}

h3_profile_prepare_base() {
  h3_profile_load_base
  main
  h3_profile_install_video_helper_suite
  h3_profile_install_refine_capability
}

h3_profile_install_requirements() {
  local node_dir="$1" requirements="$1/requirements.txt"
  [[ -f "$requirements" ]] || return 0
  "$COMFY_PYTHON" -m pip install -r "$requirements"
}

h3_profile_install_node() {
  local name="$1" repo="$2"
  install_or_update_node "$name" "$repo"
  h3_profile_install_requirements "$COMFY_DIR/custom_nodes/$name"
}

h3_profile_install_video_helper_suite() {
  local node_dir="$COMFY_DIR/custom_nodes/$VHS_NODE_NAME" actual_rev
  mkdir -p "$COMFY_DIR/custom_nodes"
  if [[ -e "$node_dir" && ! -d "$node_dir/.git" ]]; then
    h3_profile_error "Existing VideoHelperSuite path is not a git checkout: $node_dir"
    return 1
  fi
  if [[ ! -d "$node_dir/.git" ]]; then
    git clone --filter=blob:none "$VHS_NODE_REPO" "$node_dir"
  fi
  git -C "$node_dir" fetch --force --depth=1 origin "$VHS_NODE_REV"
  git -C "$node_dir" checkout --detach --force "$VHS_NODE_REV"
  actual_rev="$(git -C "$node_dir" rev-parse HEAD)"
  [[ "$actual_rev" == "$VHS_NODE_REV" ]] \
    || { h3_profile_error "VideoHelperSuite revision mismatch: $actual_rev"; return 1; }
  h3_profile_install_requirements "$node_dir"
  h3_profile_info "VideoHelperSuite pinned media preview backend installed."
}

h3_profile_install_pinned_refine_node() {
  local node_dir="$COMFY_DIR/custom_nodes/$REFINE_NODE_NAME" actual_rev
  mkdir -p "$COMFY_DIR/custom_nodes"
  if [[ -e "$node_dir" && ! -d "$node_dir/.git" ]]; then
    h3_profile_error "Existing refine node path is not a git checkout: $node_dir"
    return 1
  fi
  if [[ ! -d "$node_dir/.git" ]]; then
    git clone --filter=blob:none "$REFINE_NODE_REPO" "$node_dir"
  fi
  git -C "$node_dir" fetch --force --depth=1 origin "$REFINE_NODE_REV"
  git -C "$node_dir" checkout --detach --force "$REFINE_NODE_REV"
  actual_rev="$(git -C "$node_dir" rev-parse HEAD)"
  [[ "$actual_rev" == "$REFINE_NODE_REV" ]] \
    || { h3_profile_error "Refine custom node revision mismatch: $actual_rev"; return 1; }
  h3_profile_install_requirements "$node_dir"
}

h3_profile_install_pinned_mpi_nodes() {
  local node_dir="$COMFY_DIR/custom_nodes/$MPI_NODE_NAME" actual_rev
  mkdir -p "$COMFY_DIR/custom_nodes"
  if [[ -e "$node_dir" && ! -d "$node_dir/.git" ]]; then
    h3_profile_error "Existing MPI node path is not a git checkout: $node_dir"
    return 1
  fi
  if [[ ! -d "$node_dir/.git" ]]; then
    git clone --filter=blob:none "$MPI_NODE_REPO" "$node_dir"
  fi
  git -C "$node_dir" fetch --force --depth=1 origin "$MPI_NODE_REV"
  git -C "$node_dir" checkout --detach --force "$MPI_NODE_REV"
  actual_rev="$(git -C "$node_dir" rev-parse HEAD)"
  [[ "$actual_rev" == "$MPI_NODE_REV" ]] \
    || { h3_profile_error "MPI custom node revision mismatch: $actual_rev"; return 1; }
  h3_profile_install_requirements "$node_dir"
}

h3_profile_refine_preflight_disk() {
  local available_kb required_kb
  [[ "$REFINE_REQUIRED_FREE_GB" =~ ^[0-9]+$ ]] \
    || { h3_profile_error "REFINE_REQUIRED_FREE_GB must be an integer."; return 1; }
  available_kb="$(df -Pk "$COMFY_DIR" | awk 'NR==2 {print $4}')"
  [[ "$available_kb" =~ ^[0-9]+$ ]] \
    || { h3_profile_error "Could not determine free space for $COMFY_DIR."; return 1; }
  required_kb=$((REFINE_REQUIRED_FREE_GB * 1024 * 1024))
  if (( available_kb < required_kb )); then
    h3_profile_error "Shared H3 Refine needs at least ${REFINE_REQUIRED_FREE_GB} GB free on the ComfyUI filesystem."
    return 1
  fi
  h3_profile_info "H3 Refine disk preflight passed: $((available_kb / 1024 / 1024)) GB free."
}

h3_profile_sha256_file() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
    return
  fi
  "$COMFY_PYTHON" - "$path" <<'PY'
import hashlib
import sys

h = hashlib.sha256()
with open(sys.argv[1], "rb") as handle:
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        h.update(chunk)
print(h.hexdigest())
PY
}

h3_profile_verify_refine_model() {
  local path="$1" actual
  [[ -f "$path" ]] || return 1
  actual="$(h3_profile_sha256_file "$path")"
  if [[ "$actual" != "$REFINE_MODEL_SHA256" ]]; then
    h3_profile_warn "Refine checkpoint SHA-256 mismatch; removing invalid file: $path"
    rm -f "$path"
    return 1
  fi
  return 0
}

h3_profile_install_refine_model() {
  local model_dir="$COMFY_DIR/models/latent_upscale_models"
  local model_path="$model_dir/$REFINE_MODEL_NAME"
  mkdir -p "$model_dir"

  if h3_profile_verify_refine_model "$model_path"; then
    h3_profile_info "Verified existing H3 Refine checkpoint SHA-256."
    return 0
  fi

  h3_profile_hf_file "$REFINE_MODEL_REPO" "$REFINE_MODEL_NAME" "$model_dir"
  if ! h3_profile_verify_refine_model "$model_path"; then
    h3_profile_error "Downloaded H3 Refine checkpoint failed SHA-256 verification."
    return 1
  fi
  h3_profile_info "Verified H3 Refine checkpoint SHA-256."
}

h3_profile_install_refine_capability() {
  if [[ "${H3_INSTALL_REFINE:-1}" != "1" ]]; then
    h3_profile_info "Refine capability disabled by H3_INSTALL_REFINE=0"
    return 0
  fi
  h3_profile_refine_preflight_disk
  h3_profile_install_pinned_refine_node
  h3_profile_install_pinned_mpi_nodes
  h3_profile_install_refine_model
  h3_profile_info "Shared H3 Refine + packed AV latent persistence installed."
}

h3_profile_verify_media_preview_routes() {
  local base_url="http://127.0.0.1:${COMFY_PORT}" route status
  for route in /vhs/viewvideo /vhs/viewaudio; do
    status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${base_url}${route}" || true)"
    [[ "$status" == "204" ]] \
      || { h3_profile_error "ComfyUI media preview route $route is unavailable (HTTP ${status:-none})."; return 1; }
  done
  h3_profile_info "Browser-compatible video/audio preview routes are ready."
}

h3_profile_verify_refine_capability() {
  if [[ "${H3_INSTALL_REFINE:-1}" != "1" ]]; then
    return 0
  fi
  local object_info_url="http://127.0.0.1:${COMFY_PORT}/object_info"
  "$COMFY_PYTHON" - "$object_info_url" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
required = (
    "MinimaxH3LatentUpscaler3DRefineHandoff",
    "MpiSaveLatent",
    "MpiLoadLatent",
)
try:
    with urllib.request.urlopen(url, timeout=20) as response:
        catalog = json.load(response)
except Exception as exc:
    print(f"[ERROR] Could not read ComfyUI object catalog: {exc}", file=sys.stderr)
    raise SystemExit(1)

missing = [name for name in required if name not in catalog]
if missing:
    print(
        "[ERROR] ComfyUI did not register shared H3 Refine nodes: " + ", ".join(missing),
        file=sys.stderr,
    )
    raise SystemExit(1)

print("[OK] Shared H3 Refine and MPI latent persistence nodes are registered", file=sys.stderr)
PY
}

h3_profile_ensure_hf() {
  if ! "$COMFY_PYTHON" - <<'PY' >/dev/null 2>&1
import huggingface_hub
PY
  then
    "$COMFY_PYTHON" -m pip install "huggingface_hub>=0.34,<2"
  fi
}

h3_profile_hf_file() {
  local repo="$1" filename="$2" target_dir="$3"
  mkdir -p "$target_dir"
  h3_profile_ensure_hf
  "$COMFY_PYTHON" - "$repo" "$filename" "$target_dir" <<'PY'
import os, shutil, sys
from huggingface_hub import hf_hub_download
repo, filename, target_dir = sys.argv[1:]
os.makedirs(target_dir, exist_ok=True)
dst = os.path.join(target_dir, os.path.basename(filename))
if os.path.isfile(dst) and os.path.getsize(dst) > 0:
    print(f"[OK] already present: {dst}")
    raise SystemExit(0)
src = hf_hub_download(repo_id=repo, filename=filename)
tmp = dst + ".part"
shutil.copy2(src, tmp)
os.replace(tmp, dst)
print(f"[OK] installed: {dst}")
PY
}

h3_profile_hf_snapshot() {
  local repo="$1" pattern="$2" target_dir="$3"
  mkdir -p "$target_dir"
  h3_profile_ensure_hf
  "$COMFY_PYTHON" - "$repo" "$pattern" "$target_dir" <<'PY'
import os, sys
from huggingface_hub import snapshot_download
repo, pattern, target_dir = sys.argv[1:]
os.makedirs(target_dir, exist_ok=True)
snapshot_download(repo_id=repo, allow_patterns=[pattern], local_dir=target_dir)
print(f"[OK] snapshot installed: {repo} ({pattern}) -> {target_dir}")
PY
}

h3_profile_finish() {
  patch_h3_workflow_model_names || true
  restart_comfyui
  run_health_checks
  h3_profile_verify_media_preview_routes
  h3_profile_verify_refine_capability
}

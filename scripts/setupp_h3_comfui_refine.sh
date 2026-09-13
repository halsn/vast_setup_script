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

REFINE_REQUIRED_FREE_GB="${REFINE_REQUIRED_FREE_GB:-3}"
NODE_DIR="$COMFY_DIR/custom_nodes/Comfyui_Minimax_h3_latent_Upscaler"
NODE_REPO="https://github.com/xmarre/Comfyui_Minimax_h3_latent_Upscaler-Plus.git"
NODE_REV="620165a311de9b28a36260219fb5cd370a304e3c"
REFINE_MODEL_REPO="LBH-123-AI/Minimax_h3_latent_Upscaler"
REFINE_MODEL_NAME="minimax_h3_latent_upscaler_3d_fp16.safetensors"
REFINE_MODEL_SHA256="043e5a48e161610ef6c3ea974645220354d06fa618abca15f76d084812eb55c2"

cleanup_refine_profile() {
  h3_profile_cleanup || true
  if [[ -n "${COMMON_TMP:-}" && -f "$COMMON_TMP" ]]; then
    rm -f "$COMMON_TMP"
  fi
}
trap cleanup_refine_profile EXIT

refine_preflight_disk() {
  local available_kb required_kb
  [[ "$REFINE_REQUIRED_FREE_GB" =~ ^[0-9]+$ ]] \
    || { h3_profile_error "REFINE_REQUIRED_FREE_GB must be an integer."; return 1; }
  available_kb="$(df -Pk "$COMFY_DIR" | awk 'NR==2 {print $4}')"
  [[ "$available_kb" =~ ^[0-9]+$ ]] \
    || { h3_profile_error "Could not determine free space for $COMFY_DIR."; return 1; }
  required_kb=$((REFINE_REQUIRED_FREE_GB * 1024 * 1024))
  if (( available_kb < required_kb )); then
    h3_profile_error "H3 refine needs at least ${REFINE_REQUIRED_FREE_GB} GB free on the ComfyUI filesystem before downloading optional refine assets."
    return 1
  fi
  h3_profile_info "H3 refine disk preflight passed: $((available_kb / 1024 / 1024)) GB free."
}

install_pinned_refine_node() {
  mkdir -p "$(dirname "$NODE_DIR")"
  if [[ -e "$NODE_DIR" && ! -d "$NODE_DIR/.git" ]]; then
    h3_profile_error "Existing refine node path is not a git checkout: $NODE_DIR"
    return 1
  fi

  if [[ ! -d "$NODE_DIR/.git" ]]; then
    git clone --filter=blob:none "$NODE_REPO" "$NODE_DIR"
  fi

  git -C "$NODE_DIR" fetch --force --depth=1 origin "$NODE_REV"
  git -C "$NODE_DIR" checkout --detach --force "$NODE_REV"
  local actual_rev
  actual_rev="$(git -C "$NODE_DIR" rev-parse HEAD)"
  [[ "$actual_rev" == "$NODE_REV" ]] \
    || { h3_profile_error "Refine custom node revision mismatch: $actual_rev"; return 1; }
  h3_profile_install_requirements "$NODE_DIR"
}

sha256_file() {
  local path="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$path" | awk '{print $1}'
    return
  fi
  "$COMFY_PYTHON" - "$path" <<'PY'
import hashlib
import sys

h = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        h.update(chunk)
print(h.hexdigest())
PY
}

verify_refine_model() {
  local path="$1" actual
  [[ -f "$path" ]] || return 1
  actual="$(sha256_file "$path")"
  if [[ "$actual" != "$REFINE_MODEL_SHA256" ]]; then
    h3_profile_warn "Refine checkpoint SHA-256 mismatch; removing invalid file: $path"
    rm -f "$path"
    return 1
  fi
  return 0
}

install_refine_model() {
  local model_dir="$COMFY_DIR/models/latent_upscale_models"
  local model_path="$model_dir/$REFINE_MODEL_NAME"
  mkdir -p "$model_dir"

  if verify_refine_model "$model_path"; then
    h3_profile_info "Verified existing H3 refine checkpoint SHA-256."
    return 0
  fi

  h3_profile_hf_file "$REFINE_MODEL_REPO" "$REFINE_MODEL_NAME" "$model_dir"
  if ! verify_refine_model "$model_path"; then
    h3_profile_error "Downloaded H3 refine checkpoint failed SHA-256 verification."
    return 1
  fi
  h3_profile_info "Verified H3 refine checkpoint SHA-256."
}

main_refine() {
  h3_profile_prepare_base
  refine_preflight_disk
  install_pinned_refine_node
  install_refine_model
  h3_profile_finish
  h3_profile_info "H3 refine profile ready (pinned 3D latent upscaler + verified FP16 checkpoint)."
}

main_refine "$@"

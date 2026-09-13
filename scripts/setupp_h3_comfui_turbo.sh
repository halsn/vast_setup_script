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

H3_TURBO_REPO="${H3_TURBO_REPO:-lightx2v/Minimax-h3-Turbo}"
H3_TURBO_INSTALL_FL2VA_4="${H3_TURBO_INSTALL_FL2VA_4:-1}"
H3_TURBO_INSTALL_FL2VA_8="${H3_TURBO_INSTALL_FL2VA_8:-1}"
H3_TURBO_INSTALL_REF2VA_4="${H3_TURBO_INSTALL_REF2VA_4:-0}"
H3_TURBO_INSTALL_REF2VA_8="${H3_TURBO_INSTALL_REF2VA_8:-0}"
H3_TURBO_MIN_COMFYUI_VERSION="${H3_TURBO_MIN_COMFYUI_VERSION:-0.31.0}"
H3_TURBO_WORKFLOW_URL="${H3_TURBO_WORKFLOW_URL:-https://raw.githubusercontent.com/ModelTC/Minimax-H3-Turbo/main/example_workflows/video_minimax_h3_t2v_lightx2v_turbo.json}"

ensure_turbo_comfyui_version() {
  local current
  current="$(get_comfyui_version 2>/dev/null || true)"
  if [[ -n "$current" ]] && version_at_least "$current" "$H3_TURBO_MIN_COMFYUI_VERSION"; then
    log_ok "LightX2V Turbo ComfyUI requirement satisfied: $current"
    return 0
  fi

  if [[ "${H3_SKIP_UPGRADE:-0}" == "1" ]]; then
    die "ComfyUI ${current:-unknown} is below $H3_TURBO_MIN_COMFYUI_VERSION; LightX2V Turbo requires ComfyUI 0.31.0 or later."
  fi

  log_warn "LightX2V Turbo requires ComfyUI >= $H3_TURBO_MIN_COMFYUI_VERSION; updating ${current:-unknown}."
  stop_comfyui
  update_git_checkout "$COMFY_DIR" "ComfyUI"
  H3_COMFYUI_CORE_UPDATED=1
  update_python_dependencies

  current="$(get_comfyui_version 2>/dev/null || true)"
  [[ -n "$current" ]] || die "Could not determine ComfyUI version after the LightX2V Turbo update."
  version_at_least "$current" "$H3_TURBO_MIN_COMFYUI_VERSION" \
    || die "ComfyUI $current is below $H3_TURBO_MIN_COMFYUI_VERSION after update."
  log_ok "LightX2V Turbo ComfyUI requirement satisfied after update: $current"
}

install_turbo_workflow() {
  local workflow_dir="$COMFY_DIR/custom_nodes/ComfyUI-H3-Worker/example_workflows"
  local target="$workflow_dir/H3_Fast_Turbo.json"
  local tmp="$target.part"
  mkdir -p "$workflow_dir"
  log_info "Installing the official LightX2V T2V workflow contract."
  curl -fsSL --retry 3 --connect-timeout 15 "$H3_TURBO_WORKFLOW_URL" -o "$tmp"
  "$COMFY_PYTHON" - "$tmp" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
if not isinstance(value, dict) or not value.get("nodes"):
    raise SystemExit("LightX2V workflow is not a valid ComfyUI workflow JSON")
PY
  mv -f "$tmp" "$target"
  log_ok "LightX2V Turbo workflow installed: $target"
}

main_turbo() {
  h3_profile_prepare_base
  ensure_turbo_comfyui_version
  local dir="$COMFY_DIR/models/loras"

  [[ "$H3_TURBO_INSTALL_FL2VA_4" == "1" ]] && \
    h3_profile_hf_file "$H3_TURBO_REPO" \
      "minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors" "$dir"
  [[ "$H3_TURBO_INSTALL_FL2VA_8" == "1" ]] && \
    h3_profile_hf_file "$H3_TURBO_REPO" \
      "minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors" "$dir"
  [[ "$H3_TURBO_INSTALL_REF2VA_4" == "1" ]] && \
    h3_profile_hf_file "$H3_TURBO_REPO" \
      "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors" "$dir"
  [[ "$H3_TURBO_INSTALL_REF2VA_8" == "1" ]] && \
    h3_profile_hf_file "$H3_TURBO_REPO" \
      "minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors" "$dir"

  install_turbo_workflow
  h3_profile_finish
  log_ok "H3 Turbo profile ready (LightX2V FL2VA 4-step v1.2 + optional 8-step assets; ComfyUI core nodes)."
}

main_turbo "$@"

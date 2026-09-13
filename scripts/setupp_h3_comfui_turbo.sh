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

main_turbo() {
  h3_profile_prepare_base
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

  h3_profile_finish
  log_ok "H3 Turbo profile ready (LightX2V 4/8-step LoRAs; native ComfyUI H3 nodes)."
}

main_turbo "$@"

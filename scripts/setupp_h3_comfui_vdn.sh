#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P || true)"
COMMON="${SCRIPT_DIR:+$SCRIPT_DIR/h3_profile_common.sh}"
if [[ ! -f "$COMMON" ]]; then
  COMMON_URL="${H3_PROFILE_COMMON_URL:-https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/h3_profile_common.sh}"
  COMMON="$(mktemp)"
  trap 'rm -f "$COMMON"' EXIT
  curl -fsSL --retry 3 --connect-timeout 15 "$COMMON_URL" -o "$COMMON"
fi
# shellcheck disable=SC1090
source "$COMMON"

H3_VDN_NODE_REPO="${H3_VDN_NODE_REPO:-https://github.com/Saganaki22/ComfyUI-VDN-H3.git}"
H3_VDN_MODEL_REPO="${H3_VDN_MODEL_REPO:-OpenVDN/vdn-minimax-h3}"
H3_VDN_STAGE="${H3_VDN_STAGE:-stage-dmd-step-250}"
H3_VDN_USE_INT8_STAGE="${H3_VDN_USE_INT8_STAGE:-0}"
H3_VDN_INT8_REPO="${H3_VDN_INT8_REPO:-drbaph/vdn-minimax-h3-int8-convrot-comfyui}"

main_vdn() {
  h3_profile_prepare_base
  h3_profile_install_node "ComfyUI-VDN-H3" "$H3_VDN_NODE_REPO"

  local dir="$COMFY_DIR/models/vdn"
  if [[ "$H3_VDN_USE_INT8_STAGE" == "1" ]]; then
    h3_profile_hf_snapshot "$H3_VDN_INT8_REPO" "*" "$dir/vdn-minimax-h3-int8-convrot-comfyui"
  else
    h3_profile_hf_snapshot "$H3_VDN_MODEL_REPO" "$H3_VDN_STAGE/*" "$dir"
  fi

  h3_profile_finish
  log_ok "H3 VDN profile ready. Use the released DMD stage with 8 sampler steps and do not stack a community Turbo LoRA."
}

main_vdn "$@"

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

H3_PDD_NODE_REPO="${H3_PDD_NODE_REPO:-https://github.com/Jalen-Brunson/ComfyUI-MiniMax-H3-PDD-Acc.git}"
H3_PDD_MODEL_REPO="${H3_PDD_MODEL_REPO:-alibaba-pai/MiniMax-H3-Acc-LoRAs}"
H3_PDD_INSTALL_FL2VA="${H3_PDD_INSTALL_FL2VA:-1}"
H3_PDD_INSTALL_REF2VA="${H3_PDD_INSTALL_REF2VA:-1}"

main_pdd() {
  h3_profile_prepare_base
  h3_profile_install_node "ComfyUI-MiniMax-H3-PDD-Acc" "$H3_PDD_NODE_REPO"

  local dir="$COMFY_DIR/models/pdd_acc"
  [[ "$H3_PDD_INSTALL_FL2VA" == "1" ]] && \
    h3_profile_hf_file "$H3_PDD_MODEL_REPO" \
      "MiniMax-H3-FL2VA-Acc-8Step.safetensors" "$dir"
  [[ "$H3_PDD_INSTALL_REF2VA" == "1" ]] && \
    h3_profile_hf_file "$H3_PDD_MODEL_REPO" \
      "MiniMax-H3-Ref2VA-Acc-8Step.safetensors" "$dir"

  h3_profile_finish
  log_ok "H3 PDD profile ready (official Alibaba PDD 8-step FL2VA/Ref2VA weights)."
}

main_pdd "$@"

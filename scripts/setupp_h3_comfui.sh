#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P || true)"
COMMON="${SCRIPT_DIR:+$SCRIPT_DIR/h3_profile_common.sh}"
T8_PROMPT_ENHANCER_NODE="ComfyUI-MiniMax-H3-Prompt-Enhancer-T8"
T8_PROMPT_ENHANCER_REPO="https://github.com/T8mars/comfyui-minimax-h3-prompt-enhancer-T8.git"
T8_PROMPT_ENHANCER_REV="4fdab875fe2132168a26ccdc0527076e89c479e3"
T8_BLOCKCACHE_NODE="comfyui-minimax-h3-blockcache-T8"
T8_BLOCKCACHE_REPO="https://github.com/T8mars/comfyui-minimax-h3-blockcache-T8.git"
T8_BLOCKCACHE_REV="36336dcee1ecb49a5ee98426456aeb353c8535bd"
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

cleanup_native_profile() {
  h3_profile_cleanup || true
  if [[ -n "${COMMON_TMP:-}" && -f "$COMMON_TMP" ]]; then
    rm -f "$COMMON_TMP"
  fi
}
trap cleanup_native_profile EXIT

main_native() {
  case "${1:-}" in
    -h|--help|--version)
      h3_profile_load_base
      main "$@"
      return
      ;;
    "")
      ;;
    *)
      # Preserve the base CLI's validation/error behavior for unknown arguments.
      h3_profile_load_base
      main "$@"
      return
      ;;
  esac

  H3_MIN_COMFYUI_VERSION="0.33.0"
  export H3_MIN_COMFYUI_VERSION
  h3_profile_prepare_base
  h3_profile_install_pinned_node "$T8_PROMPT_ENHANCER_NODE" "$T8_PROMPT_ENHANCER_REPO" "$T8_PROMPT_ENHANCER_REV"
  h3_profile_install_pinned_node "$T8_BLOCKCACHE_NODE" "$T8_BLOCKCACHE_REPO" "$T8_BLOCKCACHE_REV"
  h3_profile_finish
  h3_profile_verify_t8_prompt_enhancer
  h3_profile_verify_t8_blockcache
  h3_profile_info "H3 Native profile ready (shared Refine + packed AV latent persistence enabled by default)."
}

main_native "$@"

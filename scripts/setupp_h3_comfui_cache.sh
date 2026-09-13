#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P || true)"
FAST="${SCRIPT_DIR:+$SCRIPT_DIR/setupp_h3_comfui_fast.sh}"
if [[ ! -f "$FAST" ]]; then
  FAST_URL="${H3_CACHE_FAST_URL:-https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui_fast.sh}"
  FAST="$(mktemp)"
  trap 'rm -f "$FAST"' EXIT
  curl -fsSL --retry 3 --connect-timeout 15 "$FAST_URL" -o "$FAST"
fi

H3_CACHE_METHOD="${H3_CACHE_METHOD:-spectrum}"
case "$H3_CACHE_METHOD" in
  spectrum)
    export H3_FAST_METHOD=spectrum
    export H3_FAST_INSTALL_SPECTRUM=1
    export H3_FAST_INSTALL_FIRSTBLOCK=0
    export H3_FAST_INSTALL_TURBO=0
    ;;
  firstblock)
    export H3_FAST_METHOD=firstblock
    export H3_FAST_INSTALL_SPECTRUM=0
    export H3_FAST_INSTALL_FIRSTBLOCK=1
    export H3_FAST_INSTALL_TURBO=0
    ;;
  *)
    printf '[ERROR] H3_CACHE_METHOD must be spectrum or firstblock (got %s)\n' "$H3_CACHE_METHOD" >&2
    exit 2
    ;;
esac

exec bash "$FAST" "$@"

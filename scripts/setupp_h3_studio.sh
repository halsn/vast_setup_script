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

T8_NODE_NAME="comfyui-minimax-h3-audio-T8"
T8_NODE_REPO="https://github.com/T8mars/comfyui-minimax-h3-audio-T8.git"
T8_NODE_REV="b92b12f71a4eb0a9288cbbab26a3c05db9a1c433"

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
  local target="$COMFY_DIR/custom_nodes/$T8_NODE_NAME"
  mkdir -p "$COMFY_DIR/custom_nodes"
  h3_studio_install_pinned_checkout "$T8_NODE_NAME" "$T8_NODE_REPO" "$T8_NODE_REV" "$target"
  h3_profile_install_requirements "$target"
  h3_profile_info "Pinned T8 H3 audio/dual-clock nodes installed for the open-source Studio."
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

  h3_profile_prepare_base
  h3_studio_install_runtime_nodes
  h3_studio_install_webui

  # Restart ComfyUI after the Studio-only T8 nodes are installed, then start
  # the WebUI against the verified local ComfyUI endpoint.
  h3_profile_finish
  h3_studio_write_supervisor_config
  h3_studio_start
  h3_studio_wait_ready

  h3_profile_info "H3 Studio ready on 0.0.0.0:$H3_STUDIO_PORT (upstream MIT, pinned revision)."
}

main_studio "$@"

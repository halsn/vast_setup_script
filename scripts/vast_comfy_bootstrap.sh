#!/usr/bin/env bash
set -Eeuo pipefail

# Fixed Vast.ai onstart wrapper for the official vastai/comfy template.
# It waits for the template environment, runs the existing H3 bootstrap, and
# publishes an atomic status document for the desktop client.

H3_VAST_BOOTSTRAP_LIB_ONLY="${H3_VAST_BOOTSTRAP_LIB_ONLY:-0}"
H3_BOOTSTRAP_STATUS_FILE="${H3_BOOTSTRAP_STATUS_FILE:-/run/h3/bootstrap.json}"
H3_BOOTSTRAP_LOG_FILE="${H3_BOOTSTRAP_LOG_FILE:-/var/log/h3/bootstrap.log}"
H3_BOOTSTRAP_TRACE_FILE="${H3_BOOTSTRAP_TRACE_FILE:-/run/h3/bootstrap.log}"
H3_BOOTSTRAP_TIMEOUT_SECONDS="${H3_BOOTSTRAP_TIMEOUT_SECONDS:-300}"
H3_COMFY_DIR="${H3_COMFY_DIR:-}"
H3_COMFY_PYTHON="${H3_COMFY_PYTHON:-/venv/main/bin/python3}"
H3_COMFY_SERVICE="${H3_COMFY_SERVICE:-comfyui}"
H3_BUNDLE_ROOT="${H3_BUNDLE_ROOT:-}"
H3_FAST_URL="${H3_FAST_URL:-https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui_fast.sh}"
H3_RUNTIME_REPOSITORY="${H3_RUNTIME_REPOSITORY:-https://github.com/halsn/vast_setup_script.git}"
H3_RUNTIME_ROOT="${H3_RUNTIME_ROOT:-/opt/h3-worker}"
H3_GATEWAY_PORT="${H3_GATEWAY_PORT:-8190}"
H3_COMFY_PORT="${H3_COMFY_PORT:-18188}"
H3_STATUS_PYTHON="${H3_STATUS_PYTHON:-python3}"
H3_GATEWAY_PID=""
H3_BOOTSTRAP_SUCCEEDED=0
H3_BOOTSTRAP_STAGE="starting"

_write_bootstrap_status_unlocked() {
  local stage="$1" progress="$2" message="$3" error="${4:-}"
  mkdir -p "$(dirname "$H3_BOOTSTRAP_STATUS_FILE")"
  "$H3_STATUS_PYTHON" - "$H3_BOOTSTRAP_STATUS_FILE" "$stage" "$progress" "$message" "$error" <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

path, stage, progress, message, error = sys.argv[1:]
payload = {
    "stage": stage,
    "progress": max(0.0, min(1.0, float(progress))),
    "message": message,
    "ready": stage == "ready",
    "error": error or None,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
temporary = Path(f"{path}.tmp")
temporary.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
os.replace(temporary, path)
PY
}

write_bootstrap_status() {
  local lock_path="${H3_BOOTSTRAP_STATUS_FILE}.lock"
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$lock_path"
    flock -x 9
    _write_bootstrap_status_unlocked "$@"
    flock -u 9
    exec 9>&-
    return 0
  fi
  _write_bootstrap_status_unlocked "$@"
}

wait_for_vast_comfy_base() {
  local deadline=$((SECONDS + H3_BOOTSTRAP_TIMEOUT_SECONDS))
  while true; do
    resolve_vast_comfy_base
    if command -v supervisorctl >/dev/null 2>&1 \
      && [[ -f "$H3_COMFY_DIR/main.py" ]] \
      && [[ -x "$H3_COMFY_PYTHON" ]] \
      && supervisorctl status "$H3_COMFY_SERVICE" >/dev/null 2>&1; then
      return 0
    fi
    if (( SECONDS >= deadline )); then
      return 1
    fi
    write_bootstrap_status "waiting_for_base" 0.05 "等待 Vast ComfyUI 基础环境"
    sleep 2
  done
}

require_worker_token() {
  if [[ -z "${H3_WORKER_TOKEN:-}" ]]; then
    write_bootstrap_status "bootstrap_failed" 0 "缺少 H3 Worker Token" "H3_WORKER_TOKEN 未设置"
    return 1
  fi
}

resolve_vast_comfy_base() {
  local candidate process_pid process_dir

  # The official Vast image can expose an internal copy under /opt while
  # Supervisor runs the user-facing checkout under /workspace.  Follow the
  # running ComfyUI process first so models and custom nodes are installed in
  # the directory that the service actually loads.
  if [[ -z "$H3_COMFY_DIR" ]]; then
    while IFS= read -r process_pid; do
      [[ "$process_pid" =~ ^[0-9]+$ ]] || continue
      process_dir="$(readlink -f "/proc/$process_pid/cwd" 2>/dev/null || true)"
      if [[ -f "$process_dir/main.py" ]]; then
        H3_COMFY_DIR="$process_dir"
        break
      fi
    done < <(pgrep -f 'python.*main.py' 2>/dev/null || true)
  fi

  local candidates=(
    "$H3_COMFY_DIR"
    "/workspace/ComfyUI"
    "/workspace/comfyui"
    "/opt/workspace-internal/ComfyUI"
    "/opt/ComfyUI"
    "/opt/comfyui"
    "/root/ComfyUI"
    "/root/comfyui"
    "/app/ComfyUI"
    "/app/comfyui"
  )

  if [[ ! -f "$H3_COMFY_DIR/main.py" ]]; then
    for candidate in "${candidates[@]}"; do
      if [[ -f "$candidate/main.py" ]]; then
        H3_COMFY_DIR="$candidate"
        break
      fi
    done
  fi

  if [[ ! -x "$H3_COMFY_PYTHON" ]]; then
    for candidate in \
      "/venv/main/bin/python3" \
      "/opt/miniforge3/bin/python3" \
      "/usr/local/bin/python3" \
      "/usr/bin/python3"; do
      if [[ -x "$candidate" ]]; then
        H3_COMFY_PYTHON="$candidate"
        break
      fi
    done
  fi

  if [[ -n "${COMFYUI_ARGS:-}" ]]; then
    candidate="$(sed -nE 's/.*--port[= ]+([0-9]+).*/\1/p' <<<"$COMFYUI_ARGS" | head -n 1)"
    if [[ "$candidate" =~ ^[0-9]+$ ]]; then
      H3_COMFY_PORT="$candidate"
    fi
  fi
}

download_fast_script() {
  local target="/run/h3/setupp_h3_comfui_fast.sh"
  mkdir -p "$(dirname "$target")"
  if [[ -n "$H3_BUNDLE_ROOT" && -f "$H3_BUNDLE_ROOT/scripts/setupp_h3_comfui_fast.sh" ]]; then
    cp "$H3_BUNDLE_ROOT/scripts/setupp_h3_comfui_fast.sh" "$target"
    cp "$H3_BUNDLE_ROOT/scripts/setupp_h3_comfui.sh" "$(dirname "$target")/setupp_h3_comfui.sh"
  else
    curl -fsSL --retry 5 --connect-timeout 15 "$H3_FAST_URL" -o "$target"
  fi
  chmod 700 "$target"
  printf '%s\n' "$target"
}

install_worker_runtime() {
  write_bootstrap_status "starting_gateway" 0.12 "准备 H3 Worker Gateway"
  if [[ -n "$H3_BUNDLE_ROOT" && -f "$H3_BUNDLE_ROOT/requirements-runtime.txt" ]]; then
    rm -rf "$H3_RUNTIME_ROOT"
    mkdir -p "$H3_RUNTIME_ROOT"
    cp -a "$H3_BUNDLE_ROOT/runtime" "$H3_RUNTIME_ROOT/runtime"
    cp -a "$H3_BUNDLE_ROOT/config" "$H3_RUNTIME_ROOT/config"
    cp "$H3_BUNDLE_ROOT/requirements-runtime.txt" "$H3_RUNTIME_ROOT/requirements-runtime.txt"
  elif [[ ! -d "$H3_RUNTIME_ROOT/.git" ]]; then
    rm -rf "$H3_RUNTIME_ROOT"
    git clone --depth 1 "$H3_RUNTIME_REPOSITORY" "$H3_RUNTIME_ROOT"
  fi
  "$H3_COMFY_PYTHON" -m pip install --disable-pip-version-check -r "$H3_RUNTIME_ROOT/requirements-runtime.txt"
}

start_worker_gateway() {
  local runtime_config="${H3_RUNTIME_CONFIG:-/run/h3/runtime.json}"
  H3_WORKER_TOKEN="$H3_WORKER_TOKEN" \
    PYTHONPATH="$H3_RUNTIME_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    MODEL_CACHE_DIR="$H3_COMFY_DIR/models" \
    H3_RUNTIME_CONFIG="$runtime_config" \
    H3_BOOTSTRAP_STATUS_FILE="$H3_BOOTSTRAP_STATUS_FILE" \
    H3_BOOTSTRAP_LOG_FILE="$H3_BOOTSTRAP_LOG_FILE" \
    COMFYUI_DIR="$H3_COMFY_DIR" \
    nohup "$H3_COMFY_PYTHON" -m runtime.worker_gateway \
      --listen 0.0.0.0 \
      --port "$H3_GATEWAY_PORT" \
      --upstream "http://127.0.0.1:$H3_COMFY_PORT" \
      --runtime-config "$runtime_config" \
      --bootstrap-status "$H3_BOOTSTRAP_STATUS_FILE" \
      --template-catalog "$H3_RUNTIME_ROOT/config/templates.json" \
      --comfyui-dir "$H3_COMFY_DIR" \
      >>/var/log/h3/gateway.log 2>&1 &
  H3_GATEWAY_PID=$!
}

wait_for_gateway_health() {
  local deadline=$((SECONDS + H3_BOOTSTRAP_TIMEOUT_SECONDS))
  while true; do
    if [[ -n "$H3_GATEWAY_PID" ]] && ! kill -0 "$H3_GATEWAY_PID" 2>/dev/null; then
      write_bootstrap_status "bootstrap_failed" 0 "H3 Gateway 已退出" "Gateway 进程提前退出"
      return 1
    fi
    if curl -fsS --max-time 3 \
      -H "Authorization: Bearer $H3_WORKER_TOKEN" \
      "http://127.0.0.1:$H3_GATEWAY_PORT/healthz" >/dev/null; then
      return 0
    fi
    if (( SECONDS >= deadline )); then
      return 1
    fi
    write_bootstrap_status "health_check" 0.88 "等待 H3 Gateway 健康检查"
    sleep 2
  done
}

wait_for_comfyui() {
  local deadline=$((SECONDS + H3_BOOTSTRAP_TIMEOUT_SECONDS))
  while ! curl -fsS --max-time 3 "http://127.0.0.1:$H3_COMFY_PORT/" >/dev/null 2>&1; do
    if (( SECONDS >= deadline )); then
      return 1
    fi
    write_bootstrap_status "health_check" 0.92 "等待 ComfyUI 健康检查"
    sleep 2
  done
}

wait_for_worker_ready() {
  local deadline=$((SECONDS + H3_BOOTSTRAP_TIMEOUT_SECONDS))
  while true; do
    local body
    body="$(curl -fsS --max-time 5 \
      -H "Authorization: Bearer $H3_WORKER_TOKEN" \
      "http://127.0.0.1:$H3_GATEWAY_PORT/ready" 2>/dev/null || true)"
    if H3_READY_BODY="$body" "$H3_STATUS_PYTHON" - <<'PY'
import json
import os

try:
    payload = json.loads(os.environ["H3_READY_BODY"])
except (KeyError, TypeError, ValueError):
    raise SystemExit(1)
raise SystemExit(
    0
    if payload.get("status") == "ready" and isinstance(payload.get("templates"), list)
    else 1
)
PY
    then
      return 0
    fi
    if (( SECONDS >= deadline )); then
      return 1
    fi
    write_bootstrap_status "health_check" 0.96 "等待 H3 Worker readiness"
    sleep 2
  done
}

cleanup() {
  if (( H3_BOOTSTRAP_SUCCEEDED == 0 )) && [[ -n "$H3_GATEWAY_PID" ]]; then
    kill "$H3_GATEWAY_PID" 2>/dev/null || true
  fi
}

on_exit() {
  local code=$?
  if (( code != 0 && H3_BOOTSTRAP_SUCCEEDED == 0 )); then
    write_bootstrap_status "bootstrap_failed" 0 "H3 部署失败" "阶段=${H3_BOOTSTRAP_STAGE}; exit_code=${code}" || true
  fi
  cleanup
}

run_bootstrap() {
  mkdir -p "$(dirname "$H3_BOOTSTRAP_LOG_FILE")" "$(dirname "$H3_BOOTSTRAP_TRACE_FILE")" /run/h3
  exec > >(tee -a "$H3_BOOTSTRAP_LOG_FILE" "$H3_BOOTSTRAP_TRACE_FILE") 2>&1
  H3_BOOTSTRAP_STAGE="waiting_for_base"
  write_bootstrap_status "starting" 0 "正在初始化 Vast ComfyUI"
  wait_for_vast_comfy_base || { write_bootstrap_status "bootstrap_failed" 0 "Vast 基础环境未就绪" "等待基础环境超时"; return 1; }
  H3_BOOTSTRAP_STAGE="checking_worker_token"
  require_worker_token
  H3_BOOTSTRAP_STAGE="installing_worker_runtime"
  install_worker_runtime
  H3_BOOTSTRAP_STAGE="fast_bootstrap"
  start_worker_gateway
  H3_BOOTSTRAP_STAGE="gateway_health"
  wait_for_gateway_health
  write_bootstrap_status "installing_h3" 0.2 "正在执行 H3 Fast 初始化"
  local fast_script
  fast_script="$(download_fast_script)"
  if H3_USE_VAST_COMFY_BASE=1 \
    H3_BOOTSTRAP_STATUS_FILE="$H3_BOOTSTRAP_STATUS_FILE" \
    H3_COMFY_DIR="$H3_COMFY_DIR" \
    H3_COMFY_PYTHON="$H3_COMFY_PYTHON" \
    H3_COMFY_PORT="$H3_COMFY_PORT" \
    bash "$fast_script"; then
    :
  else
    local fast_exit_code=$?
    write_bootstrap_status "bootstrap_failed" 0 "H3 Fast 初始化失败" "阶段=${H3_BOOTSTRAP_STAGE}; exit_code=${fast_exit_code}"
    return "$fast_exit_code"
  fi
  H3_BOOTSTRAP_STAGE="runtime_startup"
  write_bootstrap_status "downloading_models" 0.78 "正在校验 H3 模型"
  PYTHONPATH="$H3_RUNTIME_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    MODEL_CACHE_DIR="$H3_COMFY_DIR/models" \
    H3_BACKEND_OVERRIDE="${H3_BACKEND_OVERRIDE:-sdpa}" \
    "$H3_COMFY_PYTHON" -m runtime.startup \
      --compatibility "$H3_RUNTIME_ROOT/config/compatibility.json" \
      --model-manifest "$H3_RUNTIME_ROOT/config/models.json.example" \
      --backend-root "$H3_RUNTIME_ROOT/backends" \
      --model-cache "$H3_COMFY_DIR/models" \
      --runtime-config "${H3_RUNTIME_CONFIG:-/run/h3/runtime.json}"
  H3_BOOTSTRAP_STAGE="health_check"
  write_bootstrap_status "starting_comfyui" 0.9 "正在确认 ComfyUI 已启动"
  wait_for_comfyui
  H3_BOOTSTRAP_STAGE="worker_readiness"
  wait_for_worker_ready
  write_bootstrap_status "ready" 1 "H3 Worker 已就绪"
  H3_BOOTSTRAP_SUCCEEDED=1
}

if [[ "$H3_VAST_BOOTSTRAP_LIB_ONLY" == "1" ]]; then
  return 0 2>/dev/null || exit 0
fi

trap on_exit EXIT
run_bootstrap

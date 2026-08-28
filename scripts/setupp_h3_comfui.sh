#!/usr/bin/env bash
set -Eeuo pipefail

H3_BOOTSTRAP_VERSION="1.1.7"
H3_MODEL_REPO="Comfy-Org/MiniMax-H3"
H3_STAGE="startup"
H3_INSTALL_SAGE="${H3_INSTALL_SAGE:-1}"
H3_SAGE_REQUIRED="${H3_SAGE_REQUIRED:-1}"
H3_USE_SAGE_GLOBAL="${H3_USE_SAGE_GLOBAL:-1}"
H3_WORKFLOW_REQUIRED="${H3_WORKFLOW_REQUIRED:-1}"
H3_USE_VAST_COMFY_BASE="${H3_USE_VAST_COMFY_BASE:-auto}"
H3_CACHED_MODEL_MIN_FREE_GB="${H3_CACHED_MODEL_MIN_FREE_GB:-20}"
H3_COMFY_START_TIMEOUT_SECONDS="${H3_COMFY_START_TIMEOUT_SECONDS:-600}"
SERVICE_NAME="${H3_SERVICE_NAME:-}"
COMFY_DIR="${H3_COMFY_DIR:-}"
COMFY_PYTHON=""
COMFY_PID=""
SUPERVISOR_CONFIG=""
COMFY_PORT="8188"
GPU_VRAM_MB="0"
SAGE_STATUS="unknown"
H3_COMFYUI_CORE_UPDATED=0
NODE_WARNINGS=()
MODEL_STATUS=()
COMFY_WAS_STOPPED=0

_color_enabled() { [[ -t 2 && -z "${NO_COLOR:-}" ]]; }
_log() {
  local level="$1"; shift
  local prefix="[$level]"
  if _color_enabled; then
    case "$level" in
      OK) prefix=$'\033[32m[OK]\033[0m' ;;
      WARN) prefix=$'\033[33m[WARN]\033[0m' ;;
      ERROR) prefix=$'\033[31m[ERROR]\033[0m' ;;
      INFO) prefix=$'\033[36m[INFO]\033[0m' ;;
    esac
  fi
  printf '%s %s\n' "$prefix" "$*" >&2
}
log_info() { _log INFO "$@"; }
log_ok() { _log OK "$@"; }
log_warn() { _log WARN "$@"; }
log_error() { _log ERROR "$@"; }
die() { log_error "$*"; return 1; }

_on_error() {
  local rc=$?
  local line="${BASH_LINENO[0]:-unknown}"
  local cmd="${BASH_COMMAND:-unknown}"
  log_error "Stage '$H3_STAGE' failed at line $line: $cmd (exit $rc)"
  if (( COMFY_WAS_STOPPED == 1 )) && [[ -n "${SERVICE_NAME:-}" ]]; then
    log_warn "ComfyUI may be stopped. Recovery: supervisorctl start '$SERVICE_NAME'"
  fi
  exit "$rc"
}
trap _on_error ERR

usage() {
  cat <<'EOF'
Vast.ai ComfyUI + MiniMax H3 bootstrapper

Usage:
  bash setup_h3.sh
  bash setup_h3.sh --help
  bash setup_h3.sh --version

Environment variables:
  H3_SKIP_UPGRADE=1       Do not update the ComfyUI Git checkout.
  H3_FORCE_REDOWNLOAD=1   Download all H3 models again.
  H3_NO_SAGE=1            Skip SageAttention detection.
  H3_INSTALL_SAGE=0       Disable automatic SageAttention installation.
  H3_SAGE_REQUIRED=0      Allow startup without SageAttention if installation fails.
  H3_SAGE_VERSION=2.2.0  Override the SageAttention package version.
  H3_USE_SAGE_GLOBAL=0    Disable the global --use-sage-attention service flag.
  H3_WORKFLOW_REQUIRED=0  Allow startup if official H3 templates cannot download.
  H3_SKIP_WORKFLOWS=1     Do not install the official H3 workflow templates.
  H3_USE_VAST_COMFY_BASE=auto|0|1
                           Auto-detect the official vastai/comfy image and skip
                           its preinstalled ComfyUI dependencies and accelerators.
  H3_COMFY_DIR=/path      Override automatic ComfyUI directory discovery.
  H3_COMFY_PYTHON=/path   Override automatic ComfyUI Python discovery.
  H3_SERVICE_NAME=name    Override automatic Supervisor service discovery.

The script supports Supervisor-managed Vast.ai ComfyUI templates. It does not
install CUDA, NVIDIA drivers, or PyTorch from scratch.
EOF
}

show_version() { printf 'setup_h3.sh %s\n' "$H3_BOOTSTRAP_VERSION"; }

command_exists() { command -v "$1" >/dev/null 2>&1; }

use_vast_comfy_base() {
  case "${H3_USE_VAST_COMFY_BASE:-auto}" in
    1|true|yes) return 0 ;;
    0|false|no) return 1 ;;
    auto)
      if [[ "${IMAGE_TYPE:-}" == "vast" \
        && -x /opt/instance-tools/bin/entrypoint.sh \
        && -x /venv/main/bin/python ]]; then
        return 0
      fi
      if [[ -x /opt/instance-tools/bin/entrypoint.sh \
        && -x /venv/main/bin/python \
        && -f /etc/vast_boot.d/boot_default.sh ]]; then
        return 0
      fi
      return 1
      ;;
    *)
      die "H3_USE_VAST_COMFY_BASE must be auto, 0, or 1."
      ;;
  esac
}

find_service_name() {
  if [[ -n "${H3_SERVICE_NAME:-}" ]]; then
    printf '%s\n' "$H3_SERVICE_NAME"
    return 0
  fi
  local output line service
  output="$(supervisorctl status 2>/dev/null || true)"
  line="$(printf '%s\n' "$output" | awk 'tolower($1) ~ /comfy/ && $2 == "RUNNING" {print; exit}')"
  if [[ -z "$line" ]]; then
    line="$(printf '%s\n' "$output" | awk 'tolower($1) ~ /comfy/ {print; exit}')"
  fi
  [[ -n "$line" ]] || return 1
  service="$(awk '{print $1}' <<<"$line")"
  printf '%s\n' "$service"
}

_descendant_pids() {
  local root="$1"
  local child
  printf '%s\n' "$root"
  while read -r child; do
    [[ -n "$child" ]] || continue
    _descendant_pids "$child"
  done < <(pgrep -P "$root" 2>/dev/null || true)
}

find_comfy_process() {
  local service="$1"
  local root pid args exe
  root="$(supervisorctl pid "$service" 2>/dev/null || true)"
  if [[ "$root" =~ ^[0-9]+$ ]] && (( root > 0 )); then
    while read -r pid; do
      [[ -r "/proc/$pid/cmdline" ]] || continue
      exe="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
      [[ "${exe##*/}" == python* ]] || continue
      args="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
      if [[ "$args" =~ [Pp]ython.*main\.py ]]; then
        printf '%s\n' "$pid"
        return 0
      fi
    done < <(_descendant_pids "$root")
  fi
  while read -r pid; do
    [[ -r "/proc/$pid/cmdline" ]] || continue
    exe="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
    [[ "${exe##*/}" == python* ]] || continue
    args="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
    if [[ "$args" =~ [Pp]ython.*main\.py ]]; then
      printf '%s\n' "$pid"
      return 0
    fi
  done < <(pgrep -f '[Pp]ython.*[/ ]main\.py' 2>/dev/null || true)
  return 1
}

_find_supervisor_files() {
  find /etc/supervisor /etc/supervisord.conf /etc/supervisord.d /opt/supervisor \
    -maxdepth 4 -type f \( -name '*.conf' -o -name '*.ini' \) 2>/dev/null || true
}

find_supervisor_config() {
  local service="$1"
  local simple="${service%%:*}"
  local file
  while read -r file; do
    if grep -Eq "^[[:space:]]*\[program:${simple//./\.}\][[:space:]]*$" "$file"; then
      printf '%s\n' "$file"
      return 0
    fi
  done < <(_find_supervisor_files)
  return 1
}

extract_supervisor_command() {
  local config="$1" service="$2"
  local simple="${service%%:*}"
  python3 - "$config" "$simple" <<'PY'
import re, sys
path, service = sys.argv[1:]
section = re.compile(r"^\s*\[program:" + re.escape(service) + r"\]\s*$")
next_section = re.compile(r"^\s*\[.+\]\s*$")
in_section = False
for raw in open(path, encoding="utf-8"):
    if section.match(raw):
        in_section = True
        continue
    if in_section and next_section.match(raw):
        break
    if in_section and re.match(r"^\s*command\s*=", raw):
        print(raw.split("=", 1)[1].strip())
        raise SystemExit(0)
raise SystemExit(1)
PY
}

_parse_command_environment() {
  local command_line="$1"
  python3 - "$command_line" <<'PY'
import os, shlex, sys
parts = shlex.split(sys.argv[1])
if len(parts) >= 3 and parts[1] == "-c" and os.path.basename(parts[0]) in {"bash", "sh"}:
    parts = shlex.split(parts[2])
if parts and parts[0] in {"exec", "env"}:
    parts = parts[1:]
python_path = ""
main_path = ""
for i, p in enumerate(parts):
    base = os.path.basename(p)
    if not python_path and (base.startswith("python") or base in {"python", "python3"}):
        python_path = p
    if p.endswith("main.py"):
        main_path = p
        break
if python_path:
    print("PYTHON=" + python_path)
if main_path:
    print("MAIN=" + main_path)
PY
}

_parse_wrapper_environment() {
  local wrapper="$1"
  [[ -r "$wrapper" ]] || return 1
  python3 - "$wrapper" <<'PY'
import os, re, shlex, sys

path = sys.argv[1]
comfyui_args_port = ""
for raw in open(path, encoding="utf-8"):
    stripped = raw.strip()
    assignment = re.match(r"^COMFYUI_ARGS\s*=\s*(.*)$", stripped)
    if assignment:
        match = re.search(r"--port(?:=|\s+)(\d+)", assignment.group(1))
        if match:
            comfyui_args_port = match.group(1)
    if not stripped or stripped.startswith("#") or "main.py" not in stripped:
        continue
    parse_line = re.sub(r"(?<!\\)\\[ \t]*$", "", stripped).rstrip()
    try:
        parts = shlex.split(parse_line, comments=True)
    except ValueError:
        continue
    if parts and parts[0] in {"exec", "env"}:
        parts = parts[1:]
    python_path = ""
    main_path = ""
    port = comfyui_args_port
    for i, part in enumerate(parts):
        base = os.path.basename(part)
        if not python_path and base.startswith("python"):
            python_path = part
        if part.endswith("main.py"):
            main_path = part
        if part == "--port" and i + 1 < len(parts):
            port = parts[i + 1]
        elif part.startswith("--port="):
            port = part.split("=", 1)[1]
    if python_path:
        print("PYTHON=" + python_path)
    if main_path:
        print("MAIN=" + main_path)
    if port:
        print("PORT=" + port)
    if python_path or main_path:
        raise SystemExit(0)
raise SystemExit(1)
PY
}

find_comfy_directory() {
  local candidate main_path scan_root
  local candidates=(
    "/workspace/ComfyUI"
    "/workspace/comfyui"
    "/opt/ComfyUI"
    "/opt/comfyui"
    "/root/ComfyUI"
    "/root/comfyui"
    "/home/ComfyUI"
    "/home/comfyui"
    "/app/ComfyUI"
    "/app/comfyui"
    "/mnt/ComfyUI"
    "/mnt/comfyui"
  )
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate/main.py" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  while IFS= read -r main_path; do
    [[ -n "$main_path" ]] || continue
    candidate="${main_path%/main.py}"
    if [[ -f "$candidate/main.py" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(find /workspace /opt /root /home /mnt /app -type f -path '*/ComfyUI/main.py' 2>/dev/null | sort)
  return 1
}

is_python_executable() {
  local candidate="$1" marker
  [[ -n "$candidate" && -x "$candidate" ]] || return 1
  if ! marker="$("$candidate" -c 'import sys; print("H3_PYTHON_OK:" + str(sys.version_info[0]))' 2>/dev/null)"; then
    return 1
  fi
  [[ "$marker" == "H3_PYTHON_OK:3" ]]
}

find_comfy_python() {
  local candidate
  local candidates=(
    "${H3_COMFY_PYTHON:-}"
    "$COMFY_DIR/.venv/bin/python"
    "$COMFY_DIR/venv/bin/python"
    "$COMFY_DIR/python/bin/python"
    "$(command -v python3 2>/dev/null || true)"
    "$(command -v python 2>/dev/null || true)"
  )
  for candidate in "${candidates[@]}"; do
    if is_python_executable "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

get_gpu_vram_mb() {
  nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
    | tr -d ' ' | awk 'BEGIN{max=0} /^[0-9]+$/{if($1>max)max=$1} END{if(max>0)print max}'
}

get_gpu_inventory() {
  if nvidia-smi \
    --query-gpu=name,memory.total,power.limit,power.max_limit \
    --format=csv,noheader 2>/dev/null; then
    return 0
  fi
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null
}

get_available_disk_gb() {
  local path="${1:-/}"
  df -Pk "$path" | awk 'NR==2 {printf "%d\n", $4/1024/1024}'
}

get_system_ram_gb() {
  awk '/MemTotal:/ {printf "%d\n", $2/1024/1024}' /proc/meminfo
}

detect_comfy_port() {
  local command_line="${1:-}"
  local port
  port="$(sed -nE 's/.*--port[= ]+([0-9]+).*/\1/p' <<<"$command_line" | head -n 1)"
  printf '%s\n' "${port:-8188}"
}

detect_environment() {
  H3_STAGE="environment discovery"
  local configured_python="${H3_COMFY_PYTHON:-}"
  local process_python=""
  if [[ -z "$SERVICE_NAME" ]]; then
    SERVICE_NAME="$(find_service_name 2>/dev/null || true)"
  fi
  [[ -n "$SERVICE_NAME" ]] || die "Could not find a Supervisor service containing 'comfy'. Set H3_SERVICE_NAME."

  COMFY_PID="$(find_comfy_process "$SERVICE_NAME" 2>/dev/null || true)"
  if [[ -n "$COMFY_PID" && -r "/proc/$COMFY_PID/exe" ]]; then
    process_python="$(readlink -f "/proc/$COMFY_PID/exe")"
    if [[ -z "$COMFY_DIR" ]]; then
      COMFY_DIR="$(readlink -f "/proc/$COMFY_PID/cwd")"
    fi
    if [[ -r "/proc/$COMFY_PID/cmdline" ]]; then
      local process_command_line
      process_command_line="$(tr '\0' ' ' < "/proc/$COMFY_PID/cmdline")"
      if grep -q -- '--port' <<<"$process_command_line"; then
        COMFY_PORT="$(detect_comfy_port "$process_command_line")"
      fi
    fi
  fi

  SUPERVISOR_CONFIG="$(find_supervisor_config "$SERVICE_NAME" 2>/dev/null || true)"
  local command_line=""
  if [[ -n "$SUPERVISOR_CONFIG" ]]; then
    command_line="$(extract_supervisor_command "$SUPERVISOR_CONFIG" "$SERVICE_NAME" 2>/dev/null || true)"
    if grep -q -- '--port' <<<"$command_line"; then
      COMFY_PORT="$(detect_comfy_port "$command_line")"
    fi
    if [[ -z "$COMFY_PYTHON" || -z "$COMFY_DIR" ]]; then
      local parsed python_path main_path
      parsed="$(_parse_command_environment "$command_line" 2>/dev/null || true)"
      python_path="$(awk -F= '$1=="PYTHON"{print substr($0,index($0,"=")+1)}' <<<"$parsed")"
      main_path="$(awk -F= '$1=="MAIN"{print substr($0,index($0,"=")+1)}' <<<"$parsed")"
      if [[ -z "$COMFY_PYTHON" && -n "$python_path" ]]; then
        COMFY_PYTHON="$(command -v "$python_path" 2>/dev/null || printf '%s' "$python_path")"
      fi
      if [[ -z "$COMFY_DIR" && -n "$main_path" ]]; then
        COMFY_DIR="$(cd "$(dirname "$main_path")" 2>/dev/null && pwd -P || true)"
      fi
    fi
    if [[ -z "$COMFY_PYTHON" || -z "$COMFY_DIR" ]]; then
      local wrapper wrapper_parsed wrapper_python wrapper_main wrapper_port
      wrapper="$(_find_wrapper_script_from_command "$command_line" 2>/dev/null || true)"
      if [[ -n "$wrapper" ]]; then
        wrapper_parsed="$(_parse_wrapper_environment "$wrapper" 2>/dev/null || true)"
        wrapper_python="$(awk -F= '$1=="PYTHON"{print substr($0,index($0,"=")+1)}' <<<"$wrapper_parsed")"
        wrapper_main="$(awk -F= '$1=="MAIN"{print substr($0,index($0,"=")+1)}' <<<"$wrapper_parsed")"
        wrapper_port="$(awk -F= '$1=="PORT"{print substr($0,index($0,"=")+1)}' <<<"$wrapper_parsed")"
        if [[ -z "$COMFY_PYTHON" && -n "$wrapper_python" ]]; then
          COMFY_PYTHON="$(command -v "$wrapper_python" 2>/dev/null || printf '%s' "$wrapper_python")"
        fi
        if [[ -z "$COMFY_DIR" && -n "$wrapper_main" ]]; then
          COMFY_DIR="$(cd "$(dirname "$wrapper_main")" 2>/dev/null && pwd -P || true)"
        fi
        if [[ -n "$wrapper_port" ]]; then COMFY_PORT="$wrapper_port"; fi
      fi
    fi
  fi

  if [[ -n "${H3_COMFY_DIR:-}" ]]; then
    COMFY_DIR="$H3_COMFY_DIR"
  elif [[ -z "$COMFY_DIR" || ! -f "$COMFY_DIR/main.py" ]]; then
    COMFY_DIR="$(find_comfy_directory 2>/dev/null || true)"
  fi
  [[ -n "$COMFY_DIR" && -f "$COMFY_DIR/main.py" ]] || die "Could not auto-detect ComfyUI/main.py under /workspace, /opt, /root, /home, /mnt, or /app. Set H3_COMFY_DIR only if the template uses a custom path."
  if [[ -n "$configured_python" ]]; then
    COMFY_PYTHON="$configured_python"
  elif use_vast_comfy_base && [[ -x "/venv/main/bin/python3" ]]; then
    COMFY_PYTHON="/venv/main/bin/python3"
  elif [[ -n "$process_python" && -z "$COMFY_PYTHON" ]]; then
    COMFY_PYTHON="$process_python"
  fi
  if [[ -z "$COMFY_PYTHON" || ! -x "$COMFY_PYTHON" ]] \
    || ! is_python_executable "$COMFY_PYTHON"; then
    COMFY_PYTHON="$(find_comfy_python 2>/dev/null || true)"
  fi
  [[ -n "$COMFY_PYTHON" && -x "$COMFY_PYTHON" ]] || die "Could not locate the Python interpreter used by ComfyUI. Set H3_COMFY_PYTHON."
  is_python_executable "$COMFY_PYTHON" || die "ComfyUI interpreter is not a working Python executable: $COMFY_PYTHON"
  [[ -n "$SUPERVISOR_CONFIG" && -f "$SUPERVISOR_CONFIG" ]] || die "Could not locate the Supervisor configuration for '$SERVICE_NAME'."
  GPU_VRAM_MB="$(get_gpu_vram_mb)"
  [[ -n "$GPU_VRAM_MB" ]] || die "No NVIDIA GPU detected with nvidia-smi."

  log_ok "Supervisor service: $SERVICE_NAME"
  log_ok "ComfyUI directory: $COMFY_DIR"
  log_ok "Python: $COMFY_PYTHON"
  log_ok "Supervisor config: $SUPERVISOR_CONFIG"
  log_ok "GPU VRAM: $GPU_VRAM_MB MB"
  if use_vast_comfy_base; then
    log_ok "Vast.ai ComfyUI base: detected; preinstalled dependencies will be reused"
  else
    log_info "Vast.ai ComfyUI base: not detected; normal dependency bootstrap enabled"
  fi
}

install_system_prerequisites() {
  H3_STAGE="system prerequisites"
  local missing=()
  local cmd
  for cmd in git curl ffmpeg; do
    command_exists "$cmd" || missing+=("$cmd")
  done
  if ((${#missing[@]} == 0)); then return 0; fi
  command_exists apt-get || die "Missing commands: ${missing[*]}; apt-get is unavailable."
  local apt=(apt-get)
  if (( EUID != 0 )); then
    command_exists sudo || die "Root privileges are required to install: ${missing[*]}"
    apt=(sudo apt-get)
  fi
  "${apt[@]}" update
  DEBIAN_FRONTEND=noninteractive "${apt[@]}" install -y "${missing[@]}"
}

validate_prerequisites() {
  H3_STAGE="preflight validation"
  command_exists supervisorctl || die "supervisorctl is unavailable; use a Supervisor-managed Vast.ai template."
  install_system_prerequisites
  [[ -d "$COMFY_DIR/.git" ]] || die "$COMFY_DIR is not a Git checkout."
  local disk ram missing_models
  disk="$(get_available_disk_gb "$COMFY_DIR")"
  ram="$(get_system_ram_gb)"
  missing_models="$(get_missing_model_count)"
  if (( missing_models == 0 )); then
    (( disk >= H3_CACHED_MODEL_MIN_FREE_GB )) || die "Only ${disk} GB free; H3 models are cached, but at least ${H3_CACHED_MODEL_MIN_FREE_GB} GB must remain available."
    log_ok "H3 model cache: complete; cached models will be reused"
  else
    (( disk >= 160 )) || die "Only ${disk} GB free; ${missing_models} H3 model file(s) are missing, and at least 160 GB is required for the initial download."
    log_info "H3 model cache: ${missing_models} file(s) missing; full download disk reserve required"
  fi
  if (( ram < 64 )); then log_warn "System RAM is ${ram} GB; 64 GB or more is recommended."; fi
  log_ok "Free disk: ${disk} GB"
  log_ok "System RAM: ${ram} GB"
}

stop_comfyui() {
  H3_STAGE="stopping ComfyUI"
  local state
  state="$(supervisorctl status "$SERVICE_NAME" 2>/dev/null | awk '{print $2}' || true)"
  if [[ "$state" == "RUNNING" || "$state" == "STARTING" ]]; then
    supervisorctl stop "$SERVICE_NAME"
    COMFY_WAS_STOPPED=1
  fi
}

_remote_default_branch() {
  local repo="$1"
  local branch
  branch="$(git -C "$repo" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || true)"
  if [[ -z "$branch" ]]; then
    branch="$(git -C "$repo" ls-remote --symref origin HEAD 2>/dev/null | awk '/^ref:/ {sub("refs/heads/", "", $2); print $2; exit}' || true)"
  fi
  if [[ -z "$branch" ]]; then
    for branch in master main; do
      if git -C "$repo" show-ref --verify --quiet "refs/remotes/origin/$branch"; then
        printf '%s\n' "$branch"
        return 0
      fi
    done
    return 1
  fi
  printf '%s\n' "$branch"
}

update_git_checkout() {
  local repo="$1" label="$2"
  [[ -d "$repo/.git" ]] || { log_warn "$label at $repo is not a Git repository; skipping."; return 0; }
  log_info "Updating $label"
  git -C "$repo" fetch origin --tags --prune
  local branch current stamp
  branch="$(_remote_default_branch "$repo")" || die "Could not determine default branch for $label."
  current="$(git -C "$repo" branch --show-current)"
  if [[ -n "$(git -C "$repo" status --porcelain)" ]]; then
    stamp="$(date +%Y%m%d-%H%M%S)"
    git -C "$repo" stash push -u -m "before-h3-bootstrap-$stamp"
    log_warn "$label local changes were stashed as before-h3-bootstrap-$stamp"
  fi
  if [[ "$current" != "$branch" ]]; then
    if git -C "$repo" show-ref --verify --quiet "refs/heads/$branch"; then
      git -C "$repo" switch "$branch"
    else
      git -C "$repo" switch -c "$branch" --track "origin/$branch"
    fi
  fi
  git -C "$repo" pull --ff-only origin "$branch"
  log_ok "$label updated on branch $branch"
}

update_comfyui() {
  H3_STAGE="updating ComfyUI"
  if use_vast_comfy_base; then
    local current
    current="$(get_comfyui_version 2>/dev/null || true)"
    if [[ -n "$current" ]] && version_at_least "$current" "0.30.0"; then
      log_info "Vast.ai ComfyUI base detected with ComfyUI $current; core update skipped."
      return 0
    fi
    if [[ "${H3_SKIP_UPGRADE:-0}" == "1" ]]; then
      die "ComfyUI ${current:-unknown} is below 0.30.0; unset H3_SKIP_UPGRADE so the core can be updated."
    fi
    log_warn "Vast.ai ComfyUI base has ComfyUI ${current:-unknown}; updating to satisfy MiniMax H3 >=0.30.0."
  fi
  if [[ "${H3_SKIP_UPGRADE:-0}" == "1" ]]; then
    log_warn "H3_SKIP_UPGRADE=1; ComfyUI core update skipped."
    return 0
  fi
  update_git_checkout "$COMFY_DIR" "ComfyUI"
  H3_COMFYUI_CORE_UPDATED=1
}

update_python_dependencies() {
  H3_STAGE="updating Python dependencies"
  if use_vast_comfy_base && (( H3_COMFYUI_CORE_UPDATED == 0 )); then
    log_info "Vast.ai ComfyUI base detected; base Python dependency upgrade skipped."
    return 0
  fi
  if use_vast_comfy_base; then
    log_info "ComfyUI core was updated; installing only its non-Torch dependencies."
  fi
  local pip_version
  pip_version="$("$COMFY_PYTHON" -m pip --version)"
  log_info "Using existing system-managed pip; skipping pip self-upgrade: $pip_version"
  local requirements_file="/tmp/h3-comfyui-requirements.txt"
  grep -Ev '^(torch|torchvision|torchaudio)([<=>].*)?$' \
    "$COMFY_DIR/requirements.txt" > "$requirements_file"
  "$COMFY_PYTHON" -m pip install -U -r "$requirements_file"
  rm -f "$requirements_file"
  "$COMFY_PYTHON" -m pip install -U huggingface_hub hf_xet
}

install_or_update_node() {
  local name="$1" url="$2"
  local target="$COMFY_DIR/custom_nodes/$name"
  mkdir -p "$COMFY_DIR/custom_nodes"
  if [[ ! -e "$target" ]]; then
    if git clone --depth 1 "$url" "$target"; then
      log_ok "$name installed"
      return 0
    fi
    log_warn "$name clone failed; continuing."
    NODE_WARNINGS+=("$name clone failed")
    return 0
  fi
  if [[ ! -d "$target/.git" ]]; then
    log_warn "$name exists at $target but is not a Git repository; skipping."
    NODE_WARNINGS+=("$name is not a Git repository")
    return 0
  fi
  if update_git_checkout "$target" "$name"; then
    log_ok "$name updated"
  else
    log_warn "$name update failed; continuing."
    NODE_WARNINGS+=("$name update failed")
  fi
}

install_custom_nodes() {
  H3_STAGE="installing custom nodes"
  if use_vast_comfy_base; then
    log_info "Vast.ai ComfyUI base detected; ComfyUI-Manager is already included."
    local kjnodes_dir="$COMFY_DIR/custom_nodes/ComfyUI-KJNodes"
    if [[ -d "$kjnodes_dir/.git" ]]; then
      log_ok "ComfyUI-KJNodes already present; update skipped."
    else
      install_or_update_node "ComfyUI-KJNodes" "https://github.com/kijai/ComfyUI-KJNodes.git"
    fi
    if [[ -f "$kjnodes_dir/requirements.txt" ]]; then
      "$COMFY_PYTHON" -m pip install -r "$kjnodes_dir/requirements.txt" \
        || log_warn "Some custom-node dependencies failed: $kjnodes_dir/requirements.txt"
    fi
    return 0
  fi
  install_or_update_node "ComfyUI-Manager" "https://github.com/Comfy-Org/ComfyUI-Manager.git"
  install_or_update_node "ComfyUI-KJNodes" "https://github.com/kijai/ComfyUI-KJNodes.git"
  local req
  for req in "$COMFY_DIR/custom_nodes/ComfyUI-Manager/requirements.txt" "$COMFY_DIR/custom_nodes/ComfyUI-KJNodes/requirements.txt"; do
    if [[ -f "$req" ]]; then
      "$COMFY_PYTHON" -m pip install -r "$req" || log_warn "Some custom-node dependencies failed: $req"
    fi
  done
}

detect_sageattention() {
  if [[ "${H3_NO_SAGE:-0}" == "1" ]]; then
    printf 'disabled\n'
    return 0
  fi
  local python="${COMFY_PYTHON:-python3}"
  if "$python" -c 'import sageattention' >/dev/null 2>&1; then
    printf 'installed\n'
  else
    printf 'not-installed-no-verified-wheel\n'
  fi
}

install_sageattention() {
  H3_STAGE="installing SageAttention"
  if [[ "${H3_NO_SAGE:-0}" == "1" ]]; then
    if [[ "${H3_SAGE_REQUIRED:-0}" == "1" ]]; then
      die "H3_NO_SAGE=1 conflicts with H3_SAGE_REQUIRED=1."
    fi
    SAGE_STATUS="disabled"
    log_warn "SageAttention installation skipped by H3_NO_SAGE=1."
    return 0
  fi
  if [[ "${H3_INSTALL_SAGE:-0}" != "1" ]]; then
    SAGE_STATUS="$(detect_sageattention)"
    return 0
  fi
  if use_vast_comfy_base; then
    SAGE_STATUS="$(detect_sageattention)"
    if [[ "$SAGE_STATUS" == "installed" ]]; then
      log_ok "SageAttention provided by the Vast.ai ComfyUI base; installation skipped."
      return 0
    fi
    if [[ "${H3_SAGE_REQUIRED:-0}" == "1" ]]; then
      die "Vast.ai ComfyUI base does not provide a verified SageAttention import. Set H3_USE_VAST_COMFY_BASE=0 to allow installation."
    fi
    log_warn "Vast.ai ComfyUI base has no verified SageAttention import; continuing without installation."
    return 0
  fi
  if [[ "$(detect_sageattention)" == "installed" ]]; then
    SAGE_STATUS="installed"
    log_ok "SageAttention is already installed."
    return 0
  fi

  local version="${H3_SAGE_VERSION:-2.2.0}"
  log_info "Installing SageAttention $version without changing the existing Torch/CUDA packages."
  if "$COMFY_PYTHON" -m pip install \
    --no-build-isolation \
    "sageattention==${version}"; then
    if [[ "$(detect_sageattention)" == "installed" ]]; then
      SAGE_STATUS="installed"
      log_ok "SageAttention $version installed. Add KJNodes 'Patch Sage Attention' to the H3 workflow."
      return 0
    fi
  fi

  SAGE_STATUS="not-installed-no-verified-wheel"
  if [[ "${H3_SAGE_REQUIRED:-0}" == "1" ]]; then
    die "SageAttention installation or import verification failed. Check CUDA_HOME, nvcc, and the Torch/CUDA match."
  fi
  log_warn "SageAttention installation failed; continuing with ComfyUI's default attention."
}

install_h3_workflow_templates() {
  H3_STAGE="installing H3 workflow templates"
  if [[ "${H3_SKIP_WORKFLOWS:-0}" == "1" ]]; then
    log_warn "Official H3 workflow templates skipped by H3_SKIP_WORKFLOWS=1."
    return 0
  fi

  local template_dir="$COMFY_DIR/custom_nodes/ComfyUI-H3-Worker/example_workflows"
  local base_url="${H3_WORKFLOW_BASE_URL:-https://raw.githubusercontent.com/Comfy-Org/workflow_templates/main/templates}"
  local variant filename target tmp
  mkdir -p "$template_dir"
  if [[ ! -f "$COMFY_DIR/custom_nodes/ComfyUI-H3-Worker/__init__.py" ]]; then
    printf '%s\n' 'NODE_CLASS_MAPPINGS = {}' > "$COMFY_DIR/custom_nodes/ComfyUI-H3-Worker/__init__.py"
  fi

  for variant in t2v i2v r2v; do
    filename="video_minimax_h3_${variant}.json"
    target="$template_dir/H3_${variant^^}_Accelerated.json"
    if [[ -f "$target" && "${H3_FORCE_WORKFLOW_UPDATE:-0}" != "1" ]]; then
      log_ok "H3 workflow template already present: $variant"
      continue
    fi
    tmp="$(mktemp)"
    if curl -fsSL --retry 3 --connect-timeout 15 "$base_url/$filename" -o "$tmp" \
      && python3 - "$tmp" <<'PY'
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    json.load(handle)
PY
    then
      mv -f "$tmp" "$target"
      log_ok "Installed H3 workflow template: $variant"
    else
      rm -f "$tmp"
      if [[ "${H3_WORKFLOW_REQUIRED:-0}" == "1" ]]; then
        die "Could not install official H3 workflow template: $filename"
      fi
      log_warn "Could not install H3 workflow template: $filename"
    fi
  done
}

patch_h3_workflow_model_names() {
  H3_STAGE="patching H3 workflow model names"
  local workflow_dir="$COMFY_DIR/custom_nodes/ComfyUI-H3-Worker/example_workflows"
  local summary
  [[ -d "$workflow_dir" ]] || return 0
  if ! summary="$($COMFY_PYTHON - "$workflow_dir" <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

root = Path(sys.argv[1])
model_names = {
    "minimax_h3_fl2va_int8_convrot.safetensors": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
    "minimax_h3_ref2va_int8_convrot.safetensors": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "qwen3vl_32b_minimax_h3_int8_convrot.safetensors": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "minimax_h3_turbo_4step_ema_ckpt500.safetensors": "minimax_h3_turbo_v4_step600_ema.safetensors",
}
patched_files = 0
replaced_values = 0

def rewrite(value):
    global replaced_values
    if isinstance(value, dict):
        for key, child in value.items():
            value[key] = rewrite(child)
        return value
    if isinstance(value, list):
        return [rewrite(child) for child in value]
    if isinstance(value, str) and value in model_names:
        replaced_values += 1
        return model_names[value]
    return value

for path in sorted(root.rglob("*.json")):
    try:
        with path.open(encoding="utf-8") as handle:
            workflow = json.load(handle)
    except (OSError, json.JSONDecodeError):
        continue
    before = replaced_values
    workflow = rewrite(workflow)
    if replaced_values == before:
        continue
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(workflow, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = handle.name
    os.replace(temporary, path)
    patched_files += 1

print(f"files={patched_files} values={replaced_values}")
PY
)"; then
    die "Could not patch H3 workflow model names."
  fi
  log_ok "H3 workflow model names checked: $summary"
}

validate_acceleration_configuration() {
  if [[ "${H3_USE_SAGE_GLOBAL:-0}" == "1" && "$SAGE_STATUS" != "installed" ]]; then
    die "H3_USE_SAGE_GLOBAL=1 requires a verified SageAttention installation."
  fi
}


version_at_least() {
  local current="${1#v}" required="${2#v}"
  local first
  first="$(printf '%s\n%s\n' "$required" "$current" | sort -V | head -n 1)"
  [[ "$first" == "$required" ]]
}

get_comfyui_version() {
  local version revision
  if version="$(cd "$COMFY_DIR" && "$COMFY_PYTHON" -c 'import comfyui_version; print(comfyui_version.__version__)' 2>/dev/null)" \
    && [[ -n "$version" ]]; then
    printf '%s\n' "$version"
    return 0
  fi
  if [[ -f "$COMFY_DIR/pyproject.toml" ]] \
    && version="$("$COMFY_PYTHON" - "$COMFY_DIR/pyproject.toml" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r"(?m)^\s*version\s*=\s*[\"']([^\"']+)[\"']", text)
if match:
    print(match.group(1))
PY
)" \
    && [[ -n "$version" ]]; then
    printf '%s\n' "$version"
    return 0
  fi
  revision="$(git -C "$COMFY_DIR" describe --tags --always 2>/dev/null || true)"
  if [[ "$revision" =~ ^v?([0-9]+\.[0-9]+\.[0-9]+) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

ensure_comfyui_version() {
  local current
  current="$(get_comfyui_version 2>/dev/null || true)"
  [[ -n "$current" ]] || die "Could not determine ComfyUI core version after update."
  version_at_least "$current" "0.30.0" || die "ComfyUI $current is too old; MiniMax H3 requires 0.30.0 or later."
  log_ok "ComfyUI core version: $current"
}

model_manifest() {
  cat <<'EOF'
diffusion_models/minimax_h3_fl2va_pruned_int8_convrot.safetensors
diffusion_models/minimax_h3_ref2va_pruned_int8_convrot.safetensors
text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors
vae/minimax_h3_video_vae_fp16.safetensors
vae/minimax_h3_audio_vae_fp32.safetensors
EOF
}

model_min_bytes() {
  local path="$1"
  case "$path" in
    diffusion_models/*) printf '%s\n' 10000000000 ;;
    text_encoders/*) printf '%s\n' 8000000000 ;;
    vae/minimax_h3_video_vae_fp16.safetensors) printf '%s\n' 4000000000 ;;
    vae/minimax_h3_audio_vae_fp32.safetensors) printf '%s\n' 500000000 ;;
    vae/*) printf '%s\n' 100000000 ;;
    *) printf '%s\n' 1000000 ;;
  esac
}

model_is_complete() {
  local path="$1" min_bytes="$2"
  [[ -f "$path" ]] || return 1
  local size
  size="$(stat -c '%s' "$path" 2>/dev/null || printf 0)"
  (( size >= min_bytes ))
}

get_missing_model_count() {
  local count=0 file
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    if ! model_is_complete "$COMFY_DIR/models/$file" "$(model_min_bytes "$file")"; then
      ((count+=1))
    fi
  done < <(model_manifest)
  printf '%s\n' "$count"
}

download_model_file() {
  local repo_path="$1" min_bytes="${2:-$(model_min_bytes "$1")}" destination
  destination="$COMFY_DIR/models/$repo_path"
  mkdir -p "$(dirname "$destination")"
  if [[ "${H3_FORCE_REDOWNLOAD:-0}" != "1" ]] && model_is_complete "$destination" "$min_bytes"; then
    log_ok "Model already present: $repo_path"
    MODEL_STATUS+=("$repo_path:present")
    return 0
  fi
  if [[ -e "$destination" ]]; then
    log_warn "Existing model will be replaced only after a verified download: $destination"
  fi
  local tmp_dir tmp_file
  tmp_dir="$(mktemp -d "$(dirname "$destination")/.h3-download.XXXXXX")"
  tmp_file="$tmp_dir/model.part"
  log_info "Downloading $repo_path"
  if ! "$COMFY_PYTHON" - "$H3_MODEL_REPO" "$repo_path" "$tmp_file" <<'PY'
import os, sys
from huggingface_hub import hf_hub_download
repo_id, filename, destination = sys.argv[1:]
work_dir = os.path.dirname(destination)
source = hf_hub_download(repo_id=repo_id, filename=filename, local_dir=work_dir)
os.replace(source, destination)
PY
  then
    rm -rf "$tmp_dir"
    die "Model download failed: $repo_path"
  fi
  if ! model_is_complete "$tmp_file" "$min_bytes"; then
    rm -rf "$tmp_dir"
    die "Downloaded model is missing or too small: $repo_path"
  fi
  mv -f "$tmp_file" "$destination"
  rm -rf "$tmp_dir"
  log_ok "Installed model: $repo_path"
  MODEL_STATUS+=("$repo_path:downloaded")
}

download_h3_models() {
  H3_STAGE="downloading MiniMax H3 models"
  local file
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    download_model_file "$file" "$(model_min_bytes "$file")"
  done < <(model_manifest)
}

expected_runtime_flags() {
  local vram_mb="$1"
  if (( vram_mb >= 80000 )); then printf '%s\n' '--highvram'; fi
  printf '%s\n' '--reserve-vram' '2' '--preview-method' 'none'
  if [[ "${H3_USE_SAGE_GLOBAL:-0}" == "1" ]]; then
    printf '%s\n' '--use-sage-attention'
  fi
}


_find_wrapper_script_from_command() {
  local command_line="$1"
  python3 - "$command_line" <<'PY'
import os, shlex, sys
try:
    parts = shlex.split(sys.argv[1])
except ValueError:
    raise SystemExit(1)
for part in parts:
    if part.endswith('.sh') and os.path.isfile(part):
        print(part)
        raise SystemExit(0)
if parts and os.path.isfile(parts[0]) and os.access(parts[0], os.X_OK):
    print(parts[0])
    raise SystemExit(0)
raise SystemExit(1)
PY
}

patch_comfy_command_file() {
  local target="$1" vram_mb="$2" use_sage="${3:-0}"
  [[ -f "$target" ]] || die "ComfyUI wrapper script not found: $target"
  local line
  line="$(grep -E '[Pp]ython([^[:space:]]*)?[[:space:]].*main\.py' "$target" | head -n 1 || true)"
  [[ -n "$line" ]] || die "Wrapper script does not contain a Python main.py command: $target"
  if grep -Eq -- '(^|[[:space:]])--(lowvram|novram|gpu-only)([=[:space:]]|$)' <<<"$line"; then
    die "Conflicting ComfyUI memory flag found in wrapper script: $target"
  fi
  local backup="$target.h3-backup-$(date +%Y%m%d-%H%M%S)"
  cp -a "$target" "$backup"
  python3 - "$target" "$vram_mb" "$use_sage" <<'PY'
import re, sys
path, vram, use_sage = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1"
lines = open(path, encoding='utf-8').read().splitlines(keepends=True)
portal_guard = re.compile(r'^\s*(?:\.|source)\s+.*exit_portal\.sh.*ComfyUI.*$')
for index, raw in enumerate(lines):
    if portal_guard.match(raw.rstrip('\n')):
        newline = '\n' if raw.endswith('\n') else ''
        indent = raw[:len(raw) - len(raw.lstrip())]
        lines[index] = (
            indent
            + ': # H3 Worker keeps ComfyUI under Supervisor; Vast Portal registration is optional.'
            + newline
        )
for i, raw in enumerate(lines):
    if not re.search(r'(?i)python\S*\s+.*main\.py', raw):
        continue
    newline = '\n' if raw.endswith('\n') else ''
    value = raw.rstrip('\n')
    expected = []
    if vram >= 80000:
        expected.append((r'(?<!\S)--highvram(?!\S)', '--highvram'))
    expected.extend([
        (r'(?<!\S)--reserve-vram(?:\s+2|=2)(?!\S)', '--reserve-vram 2'),
        (r'(?<!\S)--preview-method(?:\s+none|=none)(?!\S)', '--preview-method none'),
    ])
    if use_sage:
        expected.append((r'(?<!\S)--use-sage-attention(?!\S)', '--use-sage-attention'))
    args_assignment = next(
        (
            index for index, candidate in enumerate(lines)
            if re.match(r'^\s*COMFYUI_ARGS\s*=', candidate)
        ),
        None,
    )
    if args_assignment is not None and any('$COMFYUI_ARGS' in candidate for candidate in lines[i:]):
        assignment_raw = lines[args_assignment]
        assignment_newline = '\n' if assignment_raw.endswith('\n') else ''
        assignment_value = assignment_raw.rstrip('\n')
        assignment_match = re.match(
            r'^(?P<prefix>\s*COMFYUI_ARGS\s*=\s*)(?P<quote>["\'])(?P<body>.*)(?P=quote)(?P<suffix>\s*)$',
            assignment_value,
        )
        if assignment_match:
            for pattern, _ in expected:
                value = re.sub(pattern, '', value)
            value = re.sub(r'(?<!\\)\\\s*$', r'\\', value)
            body = assignment_match.group('body')
            for pattern, _ in expected:
                body = re.sub(pattern, '', body)
            body = re.sub(r'\s+', ' ', body).strip()
            addition = ' '.join(token for _, token in expected)
            if body:
                body += ' '
            body += addition
            lines[args_assignment] = (
                assignment_match.group('prefix')
                + assignment_match.group('quote')
                + body
                + assignment_match.group('quote')
                + assignment_match.group('suffix')
                + assignment_newline
            )
            lines[i] = value.rstrip() + newline
            break
    forwarded = re.search(r'(["\'])\$(?:@|\*)\1', value)
    continuation = re.search(r'(?<!\\)\\(?=\s|$)', value)
    normalized_continuation = False
    if continuation:
        normalized_value = re.sub(r'(?<!\\)\\[ \t]*$', r'\\', value)
        normalized_continuation = normalized_value != value
        value = normalized_value
        continuation = re.search(r'(?<!\\)\\(?=\s|$)', value)
    misplaced = (
        (forwarded and any(
            (match := re.search(pattern, value)) and match.start() > forwarded.start()
            for pattern, _ in expected
        ))
        or (continuation and any(
            (match := re.search(pattern, value)) and match.start() > continuation.start()
            for pattern, _ in expected
        ))
    )
    if misplaced:
        for pattern, _ in expected:
            value = re.sub(pattern, '', value)
        wanted = [token for _, token in expected]
        forwarded = re.search(r'(["\'])\$(?:@|\*)\1', value)
        continuation = re.search(r'(?<!\\)\\(?=\s|$)', value)
    else:
        wanted = [token for pattern, token in expected if not re.search(pattern, value)]
    if wanted:
        addition = ' '.join(wanted)
        if continuation:
            value = value[:continuation.start()].rstrip() + ' ' + addition + ' ' + value[continuation.start():]
        elif forwarded:
            value = value[:forwarded.start()] + ' ' + addition + ' ' + value[forwarded.start():]
        else:
            value += ' ' + addition
        lines[i] = value + newline
    elif normalized_continuation:
        lines[i] = value + newline
    break
else:
    raise SystemExit('main.py command not found')
with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)
PY
  log_ok "ComfyUI wrapper patched; backup: $backup"
}

patch_supervisor_config() {
  local config="$1" service="$2" vram_mb="$3" use_sage="${4:-0}"
  local simple="${service%%:*}"
  [[ -f "$config" ]] || die "Supervisor config does not exist: $config"
  local command_line
  command_line="$(extract_supervisor_command "$config" "$service")" || die "No command= line found for [program:$simple]."
  if grep -Eq -- '(^|[[:space:]])--(lowvram|novram|gpu-only)([=[:space:]]|$)' <<<"$command_line"; then
    die "Conflicting ComfyUI memory flag found in Supervisor command; remove --lowvram, --novram, or --gpu-only manually."
  fi
  if [[ "$command_line" != *main.py* ]]; then
    local wrapper
    wrapper="$(_find_wrapper_script_from_command "$command_line" 2>/dev/null || true)"
    if [[ -n "$wrapper" ]]; then
      patch_comfy_command_file "$wrapper" "$vram_mb" "$use_sage"
      return 0
    fi
    log_warn "Supervisor command does not directly contain main.py and no patchable wrapper was found: $command_line"
    return 0
  fi
  local backup
  backup="$config.h3-backup-$(date +%Y%m%d-%H%M%S)"
  cp -a "$config" "$backup"
  python3 - "$config" "$simple" "$vram_mb" "$use_sage" <<'PY'
import re, shlex, sys
path, service, vram, use_sage = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4] == "1"
lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
section = re.compile(r"^\s*\[program:" + re.escape(service) + r"\]\s*$")
next_section = re.compile(r"^\s*\[.+\]\s*$")
in_section = False
changed = False
for i, raw in enumerate(lines):
    if section.match(raw):
        in_section = True
        continue
    if in_section and next_section.match(raw):
        break
    if in_section and re.match(r"^\s*command\s*=", raw):
        prefix, value = raw.split("=", 1)
        newline = "\n" if raw.endswith("\n") else ""
        value = value.rstrip("\n").strip()
        wanted = []
        if vram >= 80000 and not re.search(r"(^|\s)--highvram(?=\s|$)", value):
            wanted.append("--highvram")
        if not re.search(r"(^|\s)--reserve-vram(?:=|\s+)", value):
            wanted += ["--reserve-vram", "2"]
        if not re.search(r"(^|\s)--preview-method(?:=|\s+)", value):
            wanted += ["--preview-method", "none"]
        if use_sage and not re.search(r"(^|\s)--use-sage-attention(?=\s|$)", value):
            wanted.append("--use-sage-attention")
        if wanted:
            parts = shlex.split(value)
            if len(parts) >= 3 and parts[1] == "-c" and parts[0].rsplit("/", 1)[-1] in {"bash", "sh"}:
                parts[2] = parts[2] + " " + " ".join(shlex.quote(x) for x in wanted)
                value = shlex.join(parts)
            else:
                value = value + " " + " ".join(shlex.quote(x) for x in wanted)
            lines[i] = prefix + "=" + value + newline
            changed = True
        break
else:
    raise SystemExit("program section not found")
if changed:
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
PY
  log_ok "Supervisor config checked; backup: $backup"
}

wait_for_service() {
  local timeout="${1:-120}" elapsed=0 state
  while (( elapsed < timeout )); do
    state="$(supervisorctl status "$SERVICE_NAME" 2>/dev/null | awk '{print $2}' || true)"
    if [[ "$state" == "RUNNING" ]]; then return 0; fi
    if [[ "$state" == "FATAL" || "$state" == "BACKOFF" || "$state" == "EXITED" ]]; then return 1; fi
    sleep 2
    ((elapsed+=2))
  done
  return 1
}

print_recent_logs() {
  log_error "Recent Supervisor stderr for $SERVICE_NAME:"
  supervisorctl tail -150 "$SERVICE_NAME" stderr 2>&1 || true
  log_error "Recent Supervisor stdout for $SERVICE_NAME:"
  supervisorctl tail -150 "$SERVICE_NAME" stdout 2>&1 || true
}

restart_comfyui() {
  H3_STAGE="restarting ComfyUI"
  supervisorctl reread
  supervisorctl update
  local state action action_rc=0
  state="$(supervisorctl status "$SERVICE_NAME" 2>/dev/null | awk '{print $2}' || true)"
  if [[ "$state" == "STARTING" ]]; then
    wait_for_service 30 || true
    state="$(supervisorctl status "$SERVICE_NAME" 2>/dev/null | awk '{print $2}' || true)"
  fi
  if [[ "$state" == "RUNNING" ]]; then
    action="restart"
    if supervisorctl restart "$SERVICE_NAME"; then :; else action_rc=$?; fi
  else
    action="start"
    if supervisorctl start "$SERVICE_NAME"; then :; else action_rc=$?; fi
  fi
  if (( action_rc != 0 )); then
    log_error "Supervisor $action failed for $SERVICE_NAME (exit $action_rc)."
    supervisorctl status "$SERVICE_NAME" 2>&1 || true
    print_recent_logs
    die "Supervisor could not $action ComfyUI. Inspect the command and wrapper above."
  fi
  if ! wait_for_service "$H3_COMFY_START_TIMEOUT_SECONDS"; then
    print_recent_logs
    die "ComfyUI did not reach RUNNING state within 120 seconds."
  fi
  COMFY_WAS_STOPPED=0
  log_ok "ComfyUI is RUNNING"
}

probe_comfyui() {
  local attempts=0
  while (( attempts < 30 )); do
    if curl -fsS --max-time 3 "http://127.0.0.1:$COMFY_PORT/" >/dev/null 2>&1; then return 0; fi
    sleep 2
    ((attempts+=1))
  done
  return 1
}

get_python_runtime_info() {
  local python="${COMFY_PYTHON:-python3}"
  "$python" - <<'PY'
import platform

print("Python=" + platform.python_version())
try:
    import torch
except Exception:
    print("PyTorch=unavailable")
    print("CUDA=unavailable")
else:
    print("PyTorch=" + str(torch.__version__))
    print("CUDA=" + str(torch.version.cuda or "unavailable"))
PY
}

runtime_flags_summary() {
  local command_line="${1:-}"
  python3 - "$command_line" <<'PY'
import re, sys

command = sys.argv[1]
flags = []
if re.search(r"(?:^|\s)--highvram(?=\s|$|[\"'])", command):
    flags.append("--highvram")
match = re.search(r"(?:^|\s)--reserve-vram(?:=|\s+)([^\s\"']+)", command)
if match:
    flags.extend(["--reserve-vram", match.group(1)])
match = re.search(r"(?:^|\s)--preview-method(?:=|\s+)([^\s\"']+)", command)
if match:
    flags.extend(["--preview-method", match.group(1)])
if re.search(r"(?:^|\s)--use-sage-attention(?=\s|$)", command):
    flags.append("--use-sage-attention")
print(" ".join(flags))
PY
}

get_effective_comfy_command() {
  local command_line wrapper line
  command_line="$(extract_supervisor_command "$SUPERVISOR_CONFIG" "$SERVICE_NAME" 2>/dev/null || true)"
  if [[ "$command_line" == *main.py* ]]; then
    printf '%s\n' "$command_line"
    return 0
  fi
  wrapper="$(_find_wrapper_script_from_command "$command_line" 2>/dev/null || true)"
  if [[ -n "$wrapper" ]]; then
    line="$(grep -E '[Pp]ython([^[:space:]]*)?[[:space:]].*main\.py' "$wrapper" | head -n 1 || true)"
    [[ -n "$line" ]] && printf '%s\n' "$line" && return 0
  fi
  printf '%s\n' "$command_line"
}

run_health_checks() {
  H3_STAGE="health checks"
  local state branch version revision missing=0 file
  state="$(supervisorctl status "$SERVICE_NAME" 2>/dev/null | awk '{print $2}' || true)"
  [[ "$state" == "RUNNING" ]] || die "Supervisor state is $state, expected RUNNING."
  branch="$(git -C "$COMFY_DIR" branch --show-current)"
  if [[ -z "$branch" ]]; then
    revision="$(git -C "$COMFY_DIR" rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
    branch="detached@${revision:-unknown}"
    log_warn "ComfyUI is in detached HEAD state; using pinned revision ${revision:-unknown}."
  fi
  version="$(git -C "$COMFY_DIR" describe --tags --always 2>/dev/null || git -C "$COMFY_DIR" rev-parse --short HEAD)"
  while IFS= read -r file; do
    if ! model_is_complete "$COMFY_DIR/models/$file" "$(model_min_bytes "$file")"; then
      log_error "Missing/incomplete model: $file"
      missing=1
    fi
  done < <(model_manifest)
  (( missing == 0 )) || die "One or more H3 model files are missing."
  [[ -d "$COMFY_DIR/custom_nodes/ComfyUI-KJNodes" ]] || log_warn "ComfyUI-KJNodes is not installed."
  SAGE_STATUS="$(detect_sageattention)"
  local effective_command flags expected
  effective_command="$(get_effective_comfy_command)"
  flags="$(runtime_flags_summary "$effective_command")"
  while IFS= read -r expected; do
    [[ -n "$expected" ]] || continue
    if [[ " $flags " != *" $expected "* ]]; then
      log_warn "Expected ComfyUI runtime token is missing: $expected"
    fi
  done < <(expected_runtime_flags "$GPU_VRAM_MB")
  if probe_comfyui; then
    log_ok "ComfyUI HTTP endpoint responds on port $COMFY_PORT"
  else
    log_warn "Supervisor is RUNNING but http://127.0.0.1:$COMFY_PORT did not respond. Vast portal routing may use another internal port."
  fi
  log_ok "ComfyUI revision: $version ($branch)"
}

print_summary() {
  local version branch gpu runtime_info runtime_flags key value
  version="$(git -C "$COMFY_DIR" describe --tags --always 2>/dev/null || true)"
  branch="$(git -C "$COMFY_DIR" branch --show-current 2>/dev/null || true)"
  gpu="$(get_gpu_inventory | paste -sd ';' - || true)"
  runtime_info="$(get_python_runtime_info 2>/dev/null || true)"
  runtime_flags="$(runtime_flags_summary "$(get_effective_comfy_command)")"
  printf '\n'
  log_ok "ComfyUI: ${version:-unknown}"
  log_ok "Branch: ${branch:-unknown}"
  log_ok "Python executable: $COMFY_PYTHON"
  while IFS='=' read -r key value; do
    [[ -n "$key" ]] || continue
    log_ok "$key: ${value:-unknown}"
  done <<<"$runtime_info"
  log_ok "GPU: ${gpu:-unknown}"
  log_ok "Supervisor flags: ${runtime_flags:-not detected}"
  log_ok "FL2VA: installed"
  log_ok "Ref2VA: installed"
  log_ok "Text encoder: installed"
  log_ok "Video VAE: installed"
  log_ok "Audio VAE: installed"
  log_ok "H3 workflow templates: $COMFY_DIR/custom_nodes/ComfyUI-H3-Worker/example_workflows"
  if [[ "${H3_USE_SAGE_GLOBAL:-0}" == "1" ]]; then
    log_ok "Global SageAttention: enabled"
  else
    log_info "Global SageAttention: disabled; use H3_USE_SAGE_GLOBAL=1 for one-click H3 acceleration"
  fi
  if [[ "$SAGE_STATUS" == "installed" ]]; then
    log_ok "SageAttention: installed"
  elif [[ "$SAGE_STATUS" == "disabled" ]]; then
    log_warn "SageAttention: disabled by H3_NO_SAGE=1"
  else
    log_warn "SageAttention: no verified wheel installed; use the matching official release wheel manually."
  fi
  log_ok "ComfyUI local port: $COMFY_PORT"
  log_ok "Initialization complete. Open ComfyUI from the Vast.ai instance portal."
}

main() {
  case "${1:-}" in
    -h|--help) usage; return 0 ;;
    --version) show_version; return 0 ;;
    "") ;;
    *) usage >&2; die "Unknown argument: $1" ;;
  esac
  log_info "MiniMax H3 bootstrap v$H3_BOOTSTRAP_VERSION"
  log_info "This installs/updates ComfyUI, Manager, KJNodes, and five public H3 model files."
  detect_environment
  validate_prerequisites
  stop_comfyui
  update_comfyui
  update_python_dependencies
  ensure_comfyui_version
  install_custom_nodes
  install_sageattention
  validate_acceleration_configuration
  install_h3_workflow_templates
  download_h3_models
  patch_h3_workflow_model_names
  H3_STAGE="patching Supervisor"
  patch_supervisor_config "$SUPERVISOR_CONFIG" "$SERVICE_NAME" "$GPU_VRAM_MB" "${H3_USE_SAGE_GLOBAL:-0}"
  restart_comfyui
  run_health_checks
  print_summary
}

if [[ "${H3_BOOTSTRAP_LIB_ONLY:-0}" != "1" ]]; then
  main "$@"
fi

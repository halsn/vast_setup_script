#!/usr/bin/env bash
set -Eeuo pipefail

H3_PROFILE_BASE_URL="${H3_PROFILE_BASE_URL:-https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui.sh}"
H3_PROFILE_SCRIPT_SOURCE="${BASH_SOURCE[1]:-${BASH_SOURCE[0]:-}}"
H3_PROFILE_SCRIPT_DIR=""
if [[ -n "${H3_PROFILE_SCRIPT_SOURCE:-}" ]]; then
  H3_PROFILE_SCRIPT_DIR="$(cd "$(dirname "$H3_PROFILE_SCRIPT_SOURCE")" 2>/dev/null && pwd -P || true)"
fi
H3_PROFILE_BASE_TMP=""

h3_profile_info() { printf '[INFO] %s\n' "$*" >&2; }
h3_profile_warn() { printf '[WARN] %s\n' "$*" >&2; }
h3_profile_error() { printf '[ERROR] %s\n' "$*" >&2; return 1; }

h3_profile_cleanup() {
  if [[ -n "${H3_PROFILE_BASE_TMP:-}" && -f "$H3_PROFILE_BASE_TMP" ]]; then
    rm -f "$H3_PROFILE_BASE_TMP"
  fi
}

h3_profile_load_base() {
  local base_script="" local_base="" download_url="$H3_PROFILE_BASE_URL" cachebust
  local_base="${H3_PROFILE_SCRIPT_DIR:+$H3_PROFILE_SCRIPT_DIR/setupp_h3_comfui.sh}"

  if [[ "${H3_PROFILE_USE_LOCAL_BASE:-0}" == "1" ]]; then
    [[ -n "$local_base" && -f "$local_base" ]] \
      || { h3_profile_error "H3_PROFILE_USE_LOCAL_BASE=1 but local setupp_h3_comfui.sh was not found."; return 1; }
    base_script="$local_base"
  else
    command -v curl >/dev/null 2>&1 || { h3_profile_error "curl is required."; return 1; }
    H3_PROFILE_BASE_TMP="$(mktemp)"
    cachebust="$(date +%s%N 2>/dev/null || date +%s)"
    if [[ "$download_url" == *\?* ]]; then
      download_url="${download_url}&h3_cachebust=$cachebust"
    else
      download_url="${download_url}?h3_cachebust=$cachebust"
    fi
    h3_profile_info "Fetching stable H3 bootstrap from $H3_PROFILE_BASE_URL"
    curl -fsSL --retry 3 --connect-timeout 15 "$download_url" -o "$H3_PROFILE_BASE_TMP"
    base_script="$H3_PROFILE_BASE_TMP"
  fi

  H3_BOOTSTRAP_LIB_ONLY=1
  # shellcheck disable=SC1090
  source "$base_script"
}

h3_profile_prepare_base() {
  h3_profile_load_base
  main
}

h3_profile_install_requirements() {
  local node_dir="$1" requirements="$1/requirements.txt"
  [[ -f "$requirements" ]] || return 0
  "$COMFY_PYTHON" -m pip install -r "$requirements"
}

h3_profile_install_node() {
  local name="$1" repo="$2"
  install_or_update_node "$name" "$repo"
  h3_profile_install_requirements "$COMFY_DIR/custom_nodes/$name"
}

h3_profile_ensure_hf() {
  if ! "$COMFY_PYTHON" - <<'PY' >/dev/null 2>&1
import huggingface_hub
PY
  then
    "$COMFY_PYTHON" -m pip install "huggingface_hub>=0.34,<2"
  fi
}

h3_profile_hf_file() {
  local repo="$1" filename="$2" target_dir="$3"
  mkdir -p "$target_dir"
  h3_profile_ensure_hf
  "$COMFY_PYTHON" - "$repo" "$filename" "$target_dir" <<'PY'
import os, shutil, sys
from huggingface_hub import hf_hub_download
repo, filename, target_dir = sys.argv[1:]
os.makedirs(target_dir, exist_ok=True)
dst = os.path.join(target_dir, os.path.basename(filename))
if os.path.isfile(dst) and os.path.getsize(dst) > 0:
    print(f"[OK] already present: {dst}")
    raise SystemExit(0)
src = hf_hub_download(repo_id=repo, filename=filename)
tmp = dst + ".part"
shutil.copy2(src, tmp)
os.replace(tmp, dst)
print(f"[OK] installed: {dst}")
PY
}

h3_profile_hf_snapshot() {
  local repo="$1" pattern="$2" target_dir="$3"
  mkdir -p "$target_dir"
  h3_profile_ensure_hf
  "$COMFY_PYTHON" - "$repo" "$pattern" "$target_dir" <<'PY'
import os, sys
from huggingface_hub import snapshot_download
repo, pattern, target_dir = sys.argv[1:]
os.makedirs(target_dir, exist_ok=True)
snapshot_download(repo_id=repo, allow_patterns=[pattern], local_dir=target_dir)
print(f"[OK] snapshot installed: {repo} ({pattern}) -> {target_dir}")
PY
}

h3_profile_finish() {
  patch_h3_workflow_model_names || true
  restart_comfyui
  run_health_checks
}

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

H3_FASTH3_NODE_REPO="${H3_FASTH3_NODE_REPO:-https://github.com/barelymining/ComfyUI-MiniMax-H3-FastVideo.git}"
H3_FASTH3_MODEL_REPO="${H3_FASTH3_MODEL_REPO:-barelymining/ComfyUI-MiniMax-H3-FastVideo}"
H3_FASTH3_LORA="${H3_FASTH3_LORA:-fasth3_vsa_4-steps-v5.safetensors}"
H3_FASTH3_GATE="${H3_FASTH3_GATE:-fasth3_vsa_gate.safetensors}"
H3_FASTH3_INSTALL_VSA="${H3_FASTH3_INSTALL_VSA:-1}"

install_vsa_runtime() {
  [[ "$H3_FASTH3_INSTALL_VSA" == "1" ]] || return 0
  if "$COMFY_PYTHON" -c 'import vsa' >/dev/null 2>&1; then
    log_ok "vsa runtime already available."
    return 0
  fi

  "$COMFY_PYTHON" -m pip install "pytest>=8" "vsa==0.0.3" && return 0

  log_warn "Direct vsa install failed (common on non-Hopper GPUs); installing the Python fallback only."
  local tmp
  tmp="$(mktemp -d)"
  "$COMFY_PYTHON" -m pip download --no-deps "vsa==0.0.3" -d "$tmp"
  "$COMFY_PYTHON" - "$tmp" <<'PY'
import glob, os, shutil, site, sys, tarfile, tempfile, zipfile
src_dir = sys.argv[1]
archives = glob.glob(os.path.join(src_dir, "vsa-*"))
if not archives:
    raise SystemExit("vsa archive was not downloaded")
archive = archives[0]
extract = tempfile.mkdtemp(prefix="vsa-extract-")
if archive.endswith((".tar.gz", ".tgz")):
    with tarfile.open(archive) as tf:
        tf.extractall(extract)
elif archive.endswith(".zip"):
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(extract)
else:
    raise SystemExit(f"unsupported vsa archive: {archive}")
matches = glob.glob(os.path.join(extract, "**", "vsa", "__init__.py"), recursive=True)
if not matches:
    raise SystemExit("vsa Python package not found in source archive")
pkg = os.path.dirname(matches[0])
target_root = site.getsitepackages()[0]
target = os.path.join(target_root, "vsa")
if os.path.isdir(target):
    shutil.rmtree(target)
shutil.copytree(pkg, target)
print(f"[OK] installed vsa Python fallback: {target}")
PY
  rm -rf "$tmp"
  "$COMFY_PYTHON" -c 'import vsa; print("[OK] vsa import succeeded")'
}

main_fasth3() {
  h3_profile_prepare_base
  install_vsa_runtime
  h3_profile_install_node "ComfyUI-MiniMax-H3-FastVideo" "$H3_FASTH3_NODE_REPO"

  local dir="$COMFY_DIR/models/loras"
  h3_profile_hf_file "$H3_FASTH3_MODEL_REPO" "$H3_FASTH3_LORA" "$dir"
  h3_profile_hf_file "$H3_FASTH3_MODEL_REPO" "$H3_FASTH3_GATE" "$dir"

  h3_profile_finish
  log_ok "H3 FastH3/VSA profile ready. FL2VA only; use 4 steps, cfg 1.0, euler/simple."
}

main_fasth3 "$@"

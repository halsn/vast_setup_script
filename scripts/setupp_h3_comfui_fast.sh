#!/usr/bin/env bash
set -Eeuo pipefail

H3_FAST_VERSION="1.1.7"
H3_FAST_METHOD="${H3_FAST_METHOD:-spectrum}"
H3_FAST_INSTALL_SPECTRUM="${H3_FAST_INSTALL_SPECTRUM:-1}"
H3_FAST_INSTALL_FIRSTBLOCK="${H3_FAST_INSTALL_FIRSTBLOCK:-1}"
H3_FAST_INSTALL_TURBO="${H3_FAST_INSTALL_TURBO:-1}"
H3_FAST_INSTALL_TURBO_LORA="${H3_FAST_INSTALL_TURBO_LORA:-1}"
H3_FAST_INSTALL_SOLATTN="${H3_FAST_INSTALL_SOLATTN:-0}"
H3_FAST_WORKFLOW_REQUIRED="${H3_FAST_WORKFLOW_REQUIRED:-1}"
H3_FAST_SOLATTN_REPO="${H3_FAST_SOLATTN_REPO:-https://github.com/kijai/ComfyUI-SolAttn_triton.git}"
H3_FAST_TURBO_WORKFLOW_URL="${H3_FAST_TURBO_WORKFLOW_URL:-https://raw.githubusercontent.com/Larryvrh/ComfyUI-MiniMax-H3-Turbo/main/example_workflows/minimax_h3_t2v_turbo.json}"
H3_FAST_TURBO_LORA_NAME="${H3_FAST_TURBO_LORA_NAME:-minimax_h3_turbo_v4_step600_ema.safetensors}"
H3_FAST_TURBO_LORA_URL="${H3_FAST_TURBO_LORA_URL:-https://huggingface.co/larryvrh/MiniMax-H3-Turbo-Lora/resolve/main/minimax_h3_turbo_v4_step600_ema.safetensors}"
H3_FAST_BASE_URL="${H3_FAST_BASE_URL:-https://raw.githubusercontent.com/halsn/vast_setup_script/main/scripts/setupp_h3_comfui.sh}"
H3_USE_VAST_COMFY_BASE="${H3_USE_VAST_COMFY_BASE:-auto}"

H3_FAST_SCRIPT_SOURCE="${BASH_SOURCE[0]:-}"
FAST_SCRIPT_DIR=""
if [[ -n "$H3_FAST_SCRIPT_SOURCE" ]]; then
  FAST_SCRIPT_DIR="$(cd "$(dirname "$H3_FAST_SCRIPT_SOURCE")" 2>/dev/null && pwd -P || true)"
fi
FAST_BASE_TMP=""

fast_error() {
  printf '[ERROR] %s\n' "$*" >&2
  return 1
}

fast_usage() {
  cat <<'EOF'
Vast.ai ComfyUI + MiniMax H3 acceleration validation bootstrapper

Usage:
  bash setupp_h3_comfui_fast.sh
  bash setupp_h3_comfui_fast.sh --help
  bash setupp_h3_comfui_fast.sh --version

This wrapper runs the stable setupp_h3_comfui.sh first, then installs separate
experimental H3 acceleration nodes and writes independent workflows. It never
stacks EasyCache/LazyCache, FirstBlockCache, or Spectrum in one workflow.

Environment variables:
  H3_FAST_METHOD=spectrum|firstblock|turbo|none
                            Selected method for this validation run. Default: spectrum.
  H3_FAST_INSTALL_SPECTRUM=0
                            Skip the Spectrum MiniMax H3 node.
  H3_FAST_INSTALL_FIRSTBLOCK=0
                            Skip the MiniMax H3 FirstBlockCache node.
  H3_FAST_INSTALL_TURBO=0   Skip the MiniMax-H3-Turbo node and workflow.
  H3_FAST_INSTALL_TURBO_LORA=0
                            Skip the ~780 MB Turbo LoRA download.
  H3_FAST_INSTALL_SOLATTN=1 Install the optional Sol-Attention node.
  H3_FAST_WORKFLOW_REQUIRED=0
                            Continue if the official H3 template is unavailable.
  H3_FAST_SOLATTN_REPO=url  Override the optional Sol-Attention repository.
  H3_FAST_TURBO_LORA_URL=url
                            Override the Turbo LoRA download URL.
  H3_USE_VAST_COMFY_BASE=auto|0|1
                            Reuse the official vastai/comfy base dependencies.

  H3_Fast_Active.json       Points to the selected H3_FAST_METHOD workflow.

The stable script's H3_* variables remain available. The official vastai/comfy
base is auto-detected, so its preinstalled dependencies and accelerators are
reused; this script only adds H3-specific nodes, workflows, and models.
EOF
}

fast_show_version() { printf 'setupp_h3_comfui_fast.sh %s\n' "$H3_FAST_VERSION"; }

validate_fast_method() {
  local method="${1:-$H3_FAST_METHOD}"
  case "$method" in
    spectrum|firstblock|turbo|none) ;;
    *)
      fast_error "H3_FAST_METHOD must be one of: spectrum|firstblock|turbo|none (got '$method')"
      return 1
      ;;
  esac
}

validate_fast_configuration() {
  case "$H3_FAST_METHOD" in
    spectrum) [[ "$H3_FAST_INSTALL_SPECTRUM" == "1" ]] || fast_error "H3_FAST_METHOD=spectrum requires H3_FAST_INSTALL_SPECTRUM=1" ;;
    firstblock) [[ "$H3_FAST_INSTALL_FIRSTBLOCK" == "1" ]] || fast_error "H3_FAST_METHOD=firstblock requires H3_FAST_INSTALL_FIRSTBLOCK=1" ;;
    turbo) [[ "$H3_FAST_INSTALL_TURBO" == "1" ]] || fast_error "H3_FAST_METHOD=turbo requires H3_FAST_INSTALL_TURBO=1" ;;
    none) ;;
  esac
}

cleanup_fast_bootstrap() {
  if [[ -n "$FAST_BASE_TMP" && -f "$FAST_BASE_TMP" ]]; then
    rm -f "$FAST_BASE_TMP"
  fi
}
trap cleanup_fast_bootstrap EXIT

load_base_bootstrap() {
  local base_script="" download_url="$H3_FAST_BASE_URL" cachebust
  if [[ -n "$FAST_SCRIPT_DIR" && -f "$FAST_SCRIPT_DIR/setupp_h3_comfui.sh" ]]; then
    base_script="$FAST_SCRIPT_DIR/setupp_h3_comfui.sh"
  else
    command -v curl >/dev/null 2>&1 || fast_error "curl is required for remote execution."
    FAST_BASE_TMP="$(mktemp)"
    cachebust="$(date +%s%N 2>/dev/null || date +%s)"
    if [[ "$download_url" == *\?* ]]; then
      download_url="${download_url}&h3_cachebust=$cachebust"
    else
      download_url="${download_url}?h3_cachebust=$cachebust"
    fi
    if ! curl -fsSL --retry 3 --connect-timeout 15 "$download_url" -o "$FAST_BASE_TMP"; then
      fast_error "Could not download the stable bootstrap: $H3_FAST_BASE_URL"
      return 1
    fi
    base_script="$FAST_BASE_TMP"
  fi

  H3_BOOTSTRAP_LIB_ONLY=1
  # shellcheck disable=SC1090
  source "$base_script"
}

install_fast_node_requirements() {
  local node_dir="$1"
  local requirements="$node_dir/requirements.txt"
  if [[ -f "$requirements" ]]; then
    if "$COMFY_PYTHON" -m pip install -r "$requirements"; then
      log_ok "Installed dependencies for $(basename "$node_dir")"
    else
      log_warn "Some dependencies failed for $(basename "$node_dir"); continuing."
      NODE_WARNINGS+=("$(basename "$node_dir") dependencies failed")
    fi
  fi
}

download_turbo_lora() {
  local target="$COMFY_DIR/models/loras/$H3_FAST_TURBO_LORA_NAME"
  local partial="${target}.part"
  local minimum_bytes="${H3_FAST_TURBO_LORA_MIN_BYTES:-700000000}"
  local current_bytes=0
  mkdir -p "$(dirname "$target")"

  if [[ -s "$target" ]]; then
    current_bytes="$(wc -c < "$target")"
    if (( current_bytes >= minimum_bytes )); then
      log_ok "Turbo LoRA already present: $H3_FAST_TURBO_LORA_NAME"
      return 0
    fi
    log_warn "Existing Turbo LoRA is incomplete; resuming a fresh download."
    rm -f "$target"
  fi

  if [[ -s "$partial" ]]; then
    current_bytes="$(wc -c < "$partial")"
    if (( current_bytes >= minimum_bytes )); then
      mv -f "$partial" "$target"
      log_ok "Turbo LoRA recovered from a completed partial download."
      return 0
    fi
  fi

  log_info "Downloading Turbo LoRA (~780 MB): $H3_FAST_TURBO_LORA_NAME"
  if curl -fL --retry 3 --connect-timeout 15 -C - "$H3_FAST_TURBO_LORA_URL" -o "$partial"; then
    current_bytes="$(wc -c < "$partial")"
    if (( current_bytes >= minimum_bytes )); then
      mv -f "$partial" "$target"
      log_ok "Turbo LoRA installed: $target"
      return 0
    fi
  fi
  log_warn "Turbo LoRA download did not pass the size check; Turbo workflow may not run."
  return 1
}

install_fast_nodes() {
  H3_STAGE="installing experimental H3 accelerators"
  local node_dir

  if [[ "$H3_FAST_INSTALL_SPECTRUM" == "1" ]]; then
    install_or_update_node \
      "ComfyUI-Spectrum-MiniMax-H3" \
      "https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3.git"
  fi
  if [[ "$H3_FAST_INSTALL_FIRSTBLOCK" == "1" ]]; then
    install_or_update_node \
      "ComfyUI-MiniMaxH3-FirstBlockCache" \
      "https://github.com/duckyshell/ComfyUI-MiniMaxH3-FirstBlockCache.git"
  fi
  if [[ "$H3_FAST_INSTALL_TURBO" == "1" ]]; then
    install_or_update_node \
      "ComfyUI-MiniMax-H3-Turbo" \
      "https://github.com/Larryvrh/ComfyUI-MiniMax-H3-Turbo.git"
  fi
  if [[ "$H3_FAST_INSTALL_SOLATTN" == "1" ]]; then
    install_or_update_node "ComfyUI-SolAttn_triton" "$H3_FAST_SOLATTN_REPO"
  fi

  for node_dir in \
    "$COMFY_DIR/custom_nodes/ComfyUI-Spectrum-MiniMax-H3" \
    "$COMFY_DIR/custom_nodes/ComfyUI-MiniMaxH3-FirstBlockCache" \
    "$COMFY_DIR/custom_nodes/ComfyUI-MiniMax-H3-Turbo" \
    "$COMFY_DIR/custom_nodes/ComfyUI-SolAttn_triton"; do
    [[ -d "$node_dir" ]] && install_fast_node_requirements "$node_dir"
  done

  if [[ "$H3_FAST_INSTALL_TURBO" == "1" && "$H3_FAST_INSTALL_TURBO_LORA" == "1" ]]; then
    if ! download_turbo_lora; then
      NODE_WARNINGS+=("Turbo LoRA download failed")
    fi
  fi
}

write_patched_fast_workflow() {
  local method="$1" template="$2" target="$3"
  "$COMFY_PYTHON" - "$template" "$target" "$method" <<'PY'
import json
import os
import sys
import tempfile

template, target, method = sys.argv[1:]
with open(template, encoding="utf-8") as handle:
    workflow = json.load(handle)

subgraphs = workflow.get("definitions", {}).get("subgraphs", [])
if not subgraphs:
    raise SystemExit("official H3 workflow has no editable subgraph")
subgraph = subgraphs[0]
nodes = subgraph.get("nodes", [])
loader = next((node for node in nodes if node.get("type") == "UNETLoader"), None)
if loader is None:
    raise SystemExit("official H3 workflow has no UNETLoader")

model_links = [
    link for link in subgraph.get("links", [])
    if link.get("origin_id") == loader.get("id") and link.get("type") == "MODEL"
]
if not model_links:
    raise SystemExit("official H3 workflow has no model links to patch")

method_config = {
    "spectrum": {
        "class_type": "SpectrumApplyMiniMaxH3",
        "title": "H3 Fast - Spectrum MiniMax H3",
        "widgets_values": [True, 0.50, 1, 0.10, 2.0, 0.75, 1, 1, 8, False, "system_ram", True],
        "repo": "https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3",
        "warning": "Experimental forecast cache; validate motion and fine details against native H3.",
    },
    "firstblock": {
        "class_type": "ApplyMiniMaxH3FirstBlockCache",
        "title": "H3 Fast - FirstBlockCache",
        "widgets_values": ["H3 Fast \u2014 0.10 / max 2", 0.10, 0.10, 0.95, 2, False],
        "repo": "https://github.com/duckyshell/ComfyUI-MiniMaxH3-FirstBlockCache",
        "warning": "Experimental residual cache; do not combine with EasyCache, LazyCache, CacheDiT, or another block replacement.",
    },
}
if method not in method_config:
    raise SystemExit(f"unsupported graph patch method: {method}")
config = method_config[method]

old_ids = {link["id"] for link in model_links}
subgraph["links"] = [link for link in subgraph.get("links", []) if link.get("id") not in old_ids]
for node in nodes:
    for input_slot in node.get("inputs", []):
        if input_slot.get("link") in old_ids:
            input_slot.pop("link", None)
    for output_slot in node.get("outputs", []):
        output_slot["links"] = [link_id for link_id in (output_slot.get("links") or []) if link_id not in old_ids]

state = subgraph.setdefault("state", {})
node_id = max([int(node.get("id", 0)) for node in nodes if str(node.get("id", "0")).lstrip("-").isdigit()] + [int(state.get("lastNodeId", 0))]) + 1
next_link_id = max([int(link.get("id", 0)) for link in subgraph.get("links", [])] + [int(state.get("lastLinkId", 0))]) + 1
source_link_id = next_link_id
next_link_id += 1
target_links = []
for old_link in model_links:
    target_link_id = next_link_id
    next_link_id += 1
    target_links.append({
        "id": target_link_id,
        "origin_id": node_id,
        "origin_slot": 0,
        "target_id": old_link["target_id"],
        "target_slot": old_link["target_slot"],
        "type": "MODEL",
    })
    for node in nodes:
        if node.get("id") != old_link.get("target_id"):
            continue
        for input_slot in node.get("inputs", []):
            if input_slot.get("name") == "model" or input_slot.get("type") == "MODEL":
                input_slot["link"] = target_link_id
                break

source_link = {
    "id": source_link_id,
    "origin_id": loader["id"],
    "origin_slot": 0,
    "target_id": node_id,
    "target_slot": 0,
    "type": "MODEL",
}
subgraph["links"].extend([source_link, *target_links])
for output_slot in loader.get("outputs", []):
    if output_slot.get("type") == "MODEL":
        output_slot["links"] = [source_link_id]

loader_pos = loader.get("pos", [0, 0])
accelerator = {
    "id": node_id,
    "type": config["class_type"],
    "pos": [loader_pos[0] + 360, loader_pos[1]],
    "size": [390, 380],
    "flags": {},
    "order": int(loader.get("order", 0)) + 1,
    "mode": 0,
    "inputs": [{"localized_name": "model", "name": "model", "type": "MODEL", "link": source_link_id}],
    "outputs": [{
        "localized_name": "MODEL",
        "name": "MODEL",
        "type": "MODEL",
        "links": [link["id"] for link in target_links],
    }],
    "title": config["title"],
    "properties": {"Node name for S&R": config["class_type"]},
    "widgets_values": config["widgets_values"],
}
nodes.append(accelerator)
state["lastNodeId"] = node_id
state["lastLinkId"] = next_link_id - 1
workflow["last_node_id"] = max(int(workflow.get("last_node_id", 0)), node_id)
workflow["last_link_id"] = max(int(workflow.get("last_link_id", 0)), next_link_id - 1)
extra = workflow.setdefault("extra", {})
extra["h3_fast_method"] = method
extra["h3_fast_node"] = config["class_type"]
extra["h3_fast_repo"] = config["repo"]
extra["h3_fast_warning"] = config["warning"]

directory = os.path.dirname(os.path.abspath(target))
os.makedirs(directory, exist_ok=True)
with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as handle:
    json.dump(workflow, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
    temporary = handle.name
os.replace(temporary, target)
PY
}

copy_workflow_atomic() {
  local source="$1" target="$2" temporary
  [[ -f "$source" ]] || return 1
  temporary="$(mktemp "$target.tmp.XXXXXX")"
  if cp "$source" "$temporary"; then
    mv -f "$temporary" "$target"
    return 0
  fi
  rm -f "$temporary"
  return 1
}

patch_turbo_workflow_lora() {
  local target="$1"
  "$COMFY_PYTHON" - "$target" "$H3_FAST_TURBO_LORA_NAME" <<'PY'
import json
import os
import sys
import tempfile

path, lora_name = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    workflow = json.load(handle)

found = False
def patch_graph(graph):
    global found
    for node in graph.get("nodes", []):
        if node.get("type") != "MiniMaxH3TurboLoRA":
            continue
        values = node.setdefault("widgets_values", [])
        if values:
            values[0] = lora_name
        else:
            values.append(lora_name)
        found = True
    for subgraph in graph.get("definitions", {}).get("subgraphs", []):
        patch_graph(subgraph)

patch_graph(workflow)
if not found:
    raise SystemExit("Turbo workflow has no MiniMaxH3TurboLoRA node")

directory = os.path.dirname(os.path.abspath(path))
with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as handle:
    json.dump(workflow, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
    temporary = handle.name
os.replace(temporary, path)
PY
}

download_turbo_workflow() {
  local target="$1" temporary
  if [[ -f "$target" ]]; then
    return 0
  fi
  temporary="$(mktemp)"
  if curl -fsSL --retry 3 --connect-timeout 15 "$H3_FAST_TURBO_WORKFLOW_URL" -o "$temporary" \
    && "$COMFY_PYTHON" - "$temporary" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    json.load(handle)
PY
  then
    mv -f "$temporary" "$target"
    return 0
  fi
  rm -f "$temporary"
  return 1
}

generate_fast_workflows() {
  H3_STAGE="generating experimental H3 workflows"
  local workflow_dir="$COMFY_DIR/custom_nodes/ComfyUI-H3-Worker/example_workflows"
  local base_template="$workflow_dir/H3_T2V_Accelerated.json"
  local turbo_source="$COMFY_DIR/custom_nodes/ComfyUI-MiniMax-H3-Turbo/example_workflows/minimax_h3_t2v_turbo.json"
  mkdir -p "$workflow_dir"

  if [[ ! -f "$base_template" ]]; then
    if [[ "$H3_FAST_WORKFLOW_REQUIRED" == "1" ]]; then
      die "Official H3 T2V workflow is missing: $base_template"
    fi
    log_warn "Official H3 T2V workflow is missing; fast workflow generation skipped."
    return 0
  fi

  if [[ "$H3_FAST_INSTALL_SPECTRUM" == "1" ]]; then
    if write_patched_fast_workflow spectrum "$base_template" "$workflow_dir/H3_Fast_Spectrum.json"; then
      log_ok "Generated H3_Fast_Spectrum.json"
    else
      log_warn "Could not generate H3_Fast_Spectrum.json"
      NODE_WARNINGS+=("Spectrum workflow generation failed")
    fi
  fi
  if [[ "$H3_FAST_INSTALL_FIRSTBLOCK" == "1" ]]; then
    if write_patched_fast_workflow firstblock "$base_template" "$workflow_dir/H3_Fast_FirstBlockCache.json"; then
      log_ok "Generated H3_Fast_FirstBlockCache.json"
    else
      log_warn "Could not generate H3_Fast_FirstBlockCache.json"
      NODE_WARNINGS+=("FirstBlockCache workflow generation failed")
    fi
  fi
  if [[ "$H3_FAST_INSTALL_TURBO" == "1" ]]; then
    if [[ ! -f "$turbo_source" ]]; then
      turbo_source="$workflow_dir/.minimax_h3_t2v_turbo.json"
      download_turbo_workflow "$turbo_source" || true
    fi
    if copy_workflow_atomic "$turbo_source" "$workflow_dir/H3_Fast_Turbo.json" \
      && patch_turbo_workflow_lora "$workflow_dir/H3_Fast_Turbo.json"; then
      log_ok "Generated H3_Fast_Turbo.json with $H3_FAST_TURBO_LORA_NAME"
    else
      log_warn "Could not download or copy the MiniMax-H3-Turbo workflow"
      NODE_WARNINGS+=("Turbo workflow generation failed")
    fi
  fi

  local active_workflow=""
  case "$H3_FAST_METHOD" in
    spectrum) active_workflow="$workflow_dir/H3_Fast_Spectrum.json" ;;
    firstblock) active_workflow="$workflow_dir/H3_Fast_FirstBlockCache.json" ;;
    turbo) active_workflow="$workflow_dir/H3_Fast_Turbo.json" ;;
    none) log_info "H3_FAST_METHOD=none; no active fast workflow selected."; return 0 ;;
  esac
  if copy_workflow_atomic "$active_workflow" "$workflow_dir/H3_Fast_Active.json"; then
    log_ok "Selected H3_FAST_METHOD=$H3_FAST_METHOD as H3_Fast_Active.json"
  else
    log_warn "Selected fast workflow is unavailable: $active_workflow"
    NODE_WARNINGS+=("active fast workflow unavailable")
  fi
}

print_fast_summary() {
  log_ok "Fast validation method: $H3_FAST_METHOD"
  log_ok "Fast workflows: $COMFY_DIR/custom_nodes/ComfyUI-H3-Worker/example_workflows/H3_Fast_*.json"
  log_info "Open only one cache experiment at a time; do not stack EasyCache/LazyCache with Spectrum or FirstBlockCache."
  if [[ "$H3_FAST_INSTALL_SOLATTN" == "1" ]]; then
    log_info "Sol-Attention was requested as an optional experiment; verify its node appears before using it."
  fi
}

fast_main() {
  case "${1:-}" in
    -h|--help) fast_usage; return 0 ;;
    --version) fast_show_version; return 0 ;;
    "") ;;
    *) fast_usage >&2; fast_error "Unknown argument: $1"; return 1 ;;
  esac

  validate_fast_method "$H3_FAST_METHOD"
  validate_fast_configuration
  load_base_bootstrap
  log_info "Running stable H3 bootstrap before experimental acceleration validation."
  main
  install_fast_nodes
  generate_fast_workflows
  patch_h3_workflow_model_names
  restart_comfyui
  run_health_checks
  print_fast_summary
}

if [[ "${H3_FAST_LIB_ONLY:-0}" != "1" ]]; then
  fast_main "$@"
fi

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

H3_CACHE_METHOD="${H3_CACHE_METHOD:-spectrum}"
H3_CACHE_WORKFLOW_REQUIRED="${H3_CACHE_WORKFLOW_REQUIRED:-1}"
H3_CACHE_SPECTRUM_REPO="${H3_CACHE_SPECTRUM_REPO:-https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3.git}"
H3_CACHE_FIRSTBLOCK_REPO="${H3_CACHE_FIRSTBLOCK_REPO:-https://github.com/duckyshell/ComfyUI-MiniMaxH3-FirstBlockCache.git}"

validate_cache_method() {
  case "$H3_CACHE_METHOD" in
    spectrum|firstblock) ;;
    *)
      h3_profile_error "H3_CACHE_METHOD must be spectrum or firstblock (got '$H3_CACHE_METHOD')"
      return 2
      ;;
  esac
}

write_cache_workflow() {
  local template="$1" target="$2" method="$3"
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

configs = {
    "spectrum": {
        "class_type": "SpectrumApplyMiniMaxH3",
        "title": "H3 Cache - Spectrum",
        "widgets_values": [True, 0.50, 1, 0.10, 2.0, 0.75, 1, 1, 8, False, "system_ram", True],
        "repo": "https://github.com/xmarre/ComfyUI-Spectrum-MiniMax-H3",
    },
    "firstblock": {
        "class_type": "ApplyMiniMaxH3FirstBlockCache",
        "title": "H3 Cache - FirstBlockCache",
        "widgets_values": ["H3 Cache — 0.10 / max 2", 0.10, 0.10, 0.95, 2, False],
        "repo": "https://github.com/duckyshell/ComfyUI-MiniMaxH3-FirstBlockCache",
    },
}
config = configs[method]

old_ids = {link["id"] for link in model_links}
subgraph["links"] = [link for link in subgraph.get("links", []) if link.get("id") not in old_ids]
for node in nodes:
    for slot in node.get("inputs", []):
        if slot.get("link") in old_ids:
            slot.pop("link", None)
    for slot in node.get("outputs", []):
        slot["links"] = [link_id for link_id in (slot.get("links") or []) if link_id not in old_ids]

state = subgraph.setdefault("state", {})
node_id = max(
    [int(node.get("id", 0)) for node in nodes if str(node.get("id", "0")).lstrip("-").isdigit()]
    + [int(state.get("lastNodeId", 0))]
) + 1
next_link_id = max(
    [int(link.get("id", 0)) for link in subgraph.get("links", [])]
    + [int(state.get("lastLinkId", 0))]
) + 1
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
    target_node = next(node for node in nodes if node.get("id") == old_link.get("target_id"))
    for slot in target_node.get("inputs", []):
        if slot.get("name") == "model" or slot.get("type") == "MODEL":
            slot["link"] = target_link_id
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
for slot in loader.get("outputs", []):
    if slot.get("type") == "MODEL":
        slot["links"] = [source_link_id]

loader_pos = loader.get("pos", [0, 0])
nodes.append({
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
})
state["lastNodeId"] = node_id
state["lastLinkId"] = next_link_id - 1
workflow["last_node_id"] = max(int(workflow.get("last_node_id", 0)), node_id)
workflow["last_link_id"] = max(int(workflow.get("last_link_id", 0)), next_link_id - 1)
workflow.setdefault("extra", {})["h3_cache_method"] = method
workflow["extra"]["h3_cache_repo"] = config["repo"]

directory = os.path.dirname(os.path.abspath(target))
os.makedirs(directory, exist_ok=True)
with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as handle:
    json.dump(workflow, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
    temporary = handle.name
os.replace(temporary, target)
PY
}

generate_cache_workflow() {
  local workflow_dir="$COMFY_DIR/custom_nodes/ComfyUI-H3-Worker/example_workflows"
  local base="$workflow_dir/H3_T2V_Accelerated.json"
  local target="$workflow_dir/H3_Cache_Active.json"
  mkdir -p "$workflow_dir"

  if [[ ! -f "$base" ]]; then
    if [[ "$H3_CACHE_WORKFLOW_REQUIRED" == "1" ]]; then
      h3_profile_error "Official H3 T2V workflow is missing: $base"
      return 1
    fi
    h3_profile_warn "Official H3 T2V workflow missing; cache workflow generation skipped."
    return 0
  fi

  write_cache_workflow "$base" "$target" "$H3_CACHE_METHOD"
  log_ok "Generated H3_Cache_Active.json ($H3_CACHE_METHOD)."
}

main_cache() {
  validate_cache_method
  h3_profile_prepare_base

  case "$H3_CACHE_METHOD" in
    spectrum)
      h3_profile_install_node "ComfyUI-Spectrum-MiniMax-H3" "$H3_CACHE_SPECTRUM_REPO"
      ;;
    firstblock)
      h3_profile_install_node "ComfyUI-MiniMaxH3-FirstBlockCache" "$H3_CACHE_FIRSTBLOCK_REPO"
      ;;
  esac

  generate_cache_workflow
  h3_profile_finish
  log_ok "H3 Cache profile ready: $H3_CACHE_METHOD"
}

main_cache "$@"

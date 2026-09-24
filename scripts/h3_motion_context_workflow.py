"""Build the bounded FL2VA Motion Context URL template from the pinned example."""

import argparse
import copy
import json
from pathlib import Path
from uuid import UUID


UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
ROOT_TYPE = "MiniMaxH3ImageToVideo"
PROBE_TYPE = "MiniMaxH3MotionContextSeamProbe"
DROP_TYPES = {
    "LoadImage", "LoadVideo", "LoadAudio", "Text Multiline", "PrimitiveFloat",
    "ComfyMathExpression", "ResolutionSelector", "MarkdownNote", "Note",
    "Fast Groups Bypasser (rgthree)", "LoraLoaderModelOnly", "H3SLAAttention",
}


def _output_slot(node, name, expected_type):
    matches = [(index, output) for index, output in enumerate(node.get("outputs", []))
               if output["name"] == name]
    if len(matches) != 1 or matches[0][1]["type"] != expected_type:
        raise ValueError(f"{node['type']} output {name} must be exactly one {expected_type} output")
    return matches[0][0]


def _is_uuid(value):
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def prepare_workflow(source):
    workflow = copy.deepcopy(source)
    nodes = {node["id"]: node for node in workflow["nodes"]}
    roots = [node["id"] for node in nodes.values() if node["type"] == ROOT_TYPE]
    if len(roots) != 1:
        raise ValueError("Pinned source must have exactly one FL2VA node")
    adjacency = {node_id: set() for node_id in nodes}
    for link in workflow["links"]:
        if link[1] in nodes and link[3] in nodes:
            adjacency[link[1]].add(link[3])
            adjacency[link[3]].add(link[1])
    connected = set()
    pending = roots[:]
    while pending:
        node_id = pending.pop()
        if node_id not in connected:
            connected.add(node_id)
            pending.extend(adjacency[node_id] - connected)

    kept = {
        node_id for node_id in connected
        if nodes[node_id]["type"] not in DROP_TYPES
    }
    # Chain has no graph links. It belongs to the same FL2VA canvas group.
    group = next((g for g in workflow["groups"] if g["title"] == "FL2VA Motion Context"), None)
    if group is None:
        raise ValueError("Pinned source has no FL2VA Motion Context group")
    x, y, width, height = group["bounding"]
    for node_id, node in nodes.items():
        px, py = node["pos"][:2]
        if (node["type"] == "MiniMaxH3MotionContextChain"
                and x <= px <= x + width and y <= py <= y + height):
            kept.add(node_id)

    # The optional model-only patches are pure MODEL passthroughs.
    model_loader = next((node for node in nodes.values()
                         if node["id"] in kept and node["type"] not in {
                             ROOT_TYPE, "MiniMaxH3MotionContext", "MiniMaxH3MotionContextTrim",
                             "MiniMaxH3MotionContextLoadLatent", "MiniMaxH3MotionContextSaveLatent",
                             "MiniMaxH3MotionContextChain", "SaveVideo",
                         } and "MODEL" in [o["name"] for o in node.get("outputs", [])]), None)
    if model_loader is None:
        raise ValueError("FL2VA model loader is missing")
    sampler = next((node for node in nodes.values() if node["id"] in kept
                    and any(i["name"] == "model" for i in node.get("inputs", []))
                    and any(o["name"] == "AUDIO" for o in node.get("outputs", []))), None)
    if sampler is None:
        raise ValueError("FL2VA sampler is missing")
    removed_model = {node_id for node_id in connected if nodes[node_id]["type"] in {
        "LoraLoaderModelOnly", "H3SLAAttention"}}
    model_link = next((link for link in workflow["links"] if link[1] == model_loader["id"]
                       and link[3] in removed_model and link[5] == "MODEL"), None)
    sampler_model = next((i for i in sampler["inputs"] if i["name"] == "model"), None)
    if model_link is None or sampler_model is None:
        raise ValueError("FL2VA model patch path changed")
    old_sampler_link = sampler_model["link"]
    model_link[3] = sampler["id"]
    model_link[4] = next(i for i, inp in enumerate(sampler["inputs"]) if inp["name"] == "model")
    sampler_model["link"] = model_link[0]
    model_loader["outputs"][model_link[2]]["links"] = [model_link[0]]

    links = [link for link in workflow["links"] if link[1] in kept and link[3] in kept
             and link[0] != old_sampler_link]
    for node_id in kept:
        node = nodes[node_id]
        for inp in node.get("inputs", []):
            if inp.get("link") not in {link[0] for link in links}:
                inp["link"] = None
        for output in node.get("outputs", []):
            output["links"] = [link_id for link_id in (output.get("links") or [])
                               if any(link[0] == link_id for link in links)] or None
    model_loader["widgets_values"][0] = UNET
    model_loader["widgets_values"][1] = CLIP
    model_loader["widgets_values_named"]["unet_name"] = UNET
    model_loader["widgets_values_named"]["clip_name"] = CLIP
    image_to_video = nodes[roots[0]]
    image_to_video["widgets_values"][-1] = 73
    image_to_video["widgets_values_named"]["length"] = 73
    context = next(node for node in nodes.values() if node["id"] in kept
                   and node["type"] == "MiniMaxH3MotionContext")
    context["widgets_values"] = ["22", 24]
    context["widgets_values_named"].update(context_length="22", audio_context_length=24)
    trim = next(node for node in nodes.values() if node["id"] in kept
                and node["type"] == "MiniMaxH3MotionContextTrim")
    trim["widgets_values"][1] = 24
    trim["widgets_values_named"]["fps"] = 24
    chain = next(node for node in nodes.values() if node["id"] in kept
                 and node["type"] == "MiniMaxH3MotionContextChain")
    chain["widgets_values"] = [2]
    chain["widgets_values_named"]["segments"] = 2

    sampler_audio_slot = _output_slot(sampler, "AUDIO", "AUDIO")
    trim_audio_slot = next(i for i, inp in enumerate(trim["inputs"]) if inp["name"] == "audio")
    if trim["inputs"][trim_audio_slot]["type"] != "AUDIO":
        raise ValueError("Motion Context Trim audio input must be AUDIO")
    audio_link = next(link for link in links if link[1] == sampler["id"]
                      and link[2] == sampler_audio_slot and link[3] == trim["id"]
                      and link[4] == trim_audio_slot and link[5] == "AUDIO")
    probe_id = max(nodes) + 1
    probe_in_id = max(link[0] for link in workflow["links"]) + 1
    probe_out_id = probe_in_id + 1
    old_audio_link = audio_link[0]
    audio_link[3], audio_link[4] = probe_id, 0
    trim_audio = next(i for i in trim["inputs"] if i["name"] == "audio")
    trim_audio["link"] = probe_out_id
    links.append([probe_out_id, probe_id, 0, trim["id"], trim_audio_slot, "AUDIO"])
    probe = {
        "id": probe_id, "type": PROBE_TYPE, "pos": [x + width + 20, y + 200],
        "size": [300, 180], "flags": {}, "order": 0, "mode": 0,
        "inputs": [
            {"name": "clip_b_untrimmed", "type": "AUDIO", "link": probe_in_id},
            {"name": "trim_frames", "type": "INT", "link": None},
            {"name": "clip_a_latent", "type": "LATENT", "link": None},
            {"name": "audio_vae", "type": "VAE", "link": None},
        ],
        "outputs": [{"name": "audio", "type": "AUDIO", "links": [probe_out_id]},
                    {"name": "report", "type": "STRING", "links": None}],
        "properties": {"Node name for S&R": PROBE_TYPE}, "widgets_values": [24.0, 50.0, 40.0],
        "widgets_values_named": {"fps": 24.0, "window_ms": 50.0, "search_ms": 40.0},
    }
    audio_link[0] = probe_in_id
    sampler_audio = sampler["outputs"][sampler_audio_slot]
    sampler_audio["links"] = [probe_in_id if value == old_audio_link else value
                              for value in sampler_audio["links"]]
    # Keep the probe's timing and previous latent aligned with Motion Context.
    load_latent = next(n for n in nodes.values() if n["id"] in kept
                       and n["type"] == "MiniMaxH3MotionContextLoadLatent")
    for origin, output_name, target_name, link_type in (
        (context, "trim_frames", "trim_frames", "INT"),
        (load_latent, "LATENT", "clip_a_latent", "LATENT"),
        (model_loader, "VAE_1", "audio_vae", "VAE"),
    ):
        origin_slot = _output_slot(origin, output_name, link_type)
        new_id = max(link[0] for link in links) + 1
        input_slot = next(i for i, inp in enumerate(probe["inputs"]) if inp["name"] == target_name)
        if probe["inputs"][input_slot]["type"] != link_type:
            raise ValueError(f"Seam Probe input {target_name} must be {link_type}")
        links.append([new_id, origin["id"], origin_slot, probe_id, input_slot, link_type])
        probe["inputs"][input_slot]["link"] = new_id
        if origin["outputs"][origin_slot].get("links") is None:
            origin["outputs"][origin_slot]["links"] = []
        origin["outputs"][origin_slot]["links"].append(new_id)
    for link in links:
        if link[1] == probe_id or link[3] == probe_id:
            source_node = probe if link[1] == probe_id else nodes[link[1]]
            target_node = probe if link[3] == probe_id else nodes[link[3]]
            if (source_node["outputs"][link[2]]["type"] != link[5]
                    or target_node["inputs"][link[4]]["type"] != link[5]):
                raise ValueError("Seam Probe link endpoint types do not match")
    workflow["nodes"] = [node for node in workflow["nodes"] if node["id"] in kept] + [probe]
    workflow["links"] = links
    workflow["groups"] = [g for g in workflow["groups"] if g["title"] in {
        "MiniMax H3 FL2VA", "FL2VA Motion Context"}]
    definitions = workflow["definitions"]["subgraphs"]
    by_definition = {item["id"]: item for item in definitions}
    def referenced_definition(node):
        node_type = node["type"]
        if _is_uuid(node_type):
            if node_type not in by_definition:
                raise ValueError(f"Missing subgraph definition: {node_type}")
            return node_type
        return None

    required = {ref for node in workflow["nodes"] if (ref := referenced_definition(node))}
    pending = list(required)
    while pending:
        for node in by_definition[pending.pop()]["nodes"]:
            ref = referenced_definition(node)
            if ref and ref not in required:
                required.add(ref)
                pending.append(ref)
    workflow["definitions"]["subgraphs"] = [item for item in definitions if item["id"] in required]
    loader_definition = by_definition[model_loader["type"]]
    for node in loader_definition["nodes"]:
        if node["type"] == "UNETLoader":
            node["widgets_values"][0] = UNET
            node["widgets_values_named"]["unet_name"] = UNET
        elif node["type"] == "CLIPLoader":
            node["widgets_values"][0] = CLIP
            node["widgets_values_named"]["clip_name"] = CLIP
    workflow["last_node_id"] = probe_id
    workflow["last_link_id"] = max(link[0] for link in links)
    return workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    args.destination.write_text(json.dumps(prepare_workflow(json.loads(
        args.source.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

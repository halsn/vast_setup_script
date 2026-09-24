"""Offline checks for the pinned Motion Context smoke workflow."""

import json
import importlib.util
from pathlib import Path
import subprocess
import sys
from uuid import UUID


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests/fixtures/h3_motion_context_workflow.json"


def test_cli_writes_loadable_url_template(tmp_path):
    destination = tmp_path / "h3_motion_context_smoke.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/h3_motion_context_workflow.py"),
                    str(SOURCE), str(destination)], check=True)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["nodes"] and payload["links"]
    assert len([node for node in payload["nodes"] if node["type"] == "MiniMaxH3MotionContextSeamProbe"]) == 1


def test_fl2va_smoke_workflow_is_self_contained():
    module_path = ROOT / "scripts/h3_motion_context_workflow.py"
    assert module_path.is_file()
    spec = importlib.util.spec_from_file_location("h3_motion_context_workflow", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prepare_workflow = module.prepare_workflow
    result = prepare_workflow(json.loads(SOURCE.read_text(encoding="utf-8")))
    nodes = result["nodes"]
    types = [node["type"] for node in nodes]
    assert types.count("MiniMaxH3ImageToVideo") == 1
    assert types.count("MiniMaxH3MotionContext") == 1
    assert types.count("MiniMaxH3MotionContextChain") == 1
    assert types.count("MiniMaxH3MotionContextLoadLatent") == 1
    assert types.count("MiniMaxH3MotionContextSaveLatent") == 1
    assert types.count("MiniMaxH3MotionContextTrim") == 1
    assert types.count("MiniMaxH3MotionContextSeamProbe") == 1
    assert "MiniMaxH3ReferenceToVideo" not in types
    assert "LoraLoaderModelOnly" not in types
    assert "H3SLAAttention" not in types
    assert "Fast Groups Bypasser (rgthree)" not in types
    assert not any(node["type"] in {"LoadImage", "LoadVideo", "LoadAudio"} for node in nodes)

    by_type = {node["type"]: node for node in nodes}
    assert by_type["MiniMaxH3ImageToVideo"]["widgets_values"][-1] == 73
    assert by_type["MiniMaxH3MotionContext"]["widgets_values"] == ["22", 24]
    assert by_type["MiniMaxH3MotionContextChain"]["widgets_values"] == [2]
    assert by_type["MiniMaxH3MotionContextTrim"]["widgets_values"][1] == 24
    model_values = str(by_type[next(t for t in types if t.count("-") == 4)]["widgets_values"])
    assert "minimax_h3_fl2va_pruned_int8_convrot.safetensors" in model_values
    assert "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors" in model_values

    links = result["links"]
    assert len({link[0] for link in links}) == len(links)
    node_ids = {node["id"] for node in nodes}
    assert all(link[1] in node_ids and link[3] in node_ids for link in links)
    assert all(node_input.get("link") is None or any(link[0] == node_input["link"] for link in links)
               for node in nodes for node_input in node.get("inputs", []))
    assert all(link_id in {link[0] for link in links} for node in nodes
               for output in node.get("outputs", []) for link_id in (output.get("links") or []))
    assert all(link[0] in (next(node for node in nodes if node["id"] == link[1])["outputs"][link[2]].get("links") or [])
               and next(node for node in nodes if node["id"] == link[3])["inputs"][link[4]]["link"] == link[0]
               for link in links)
    group = next(group for group in result["groups"] if group["title"] == "FL2VA Motion Context")
    x, y, width, height = group["bounding"]
    for kind in ("MiniMaxH3MotionContextChain", "MiniMaxH3MotionContextLoadLatent", "MiniMaxH3MotionContextSaveLatent"):
        px, py = by_type[kind]["pos"][:2]
        assert x <= px <= x + width and y <= py <= y + height

    sampler = by_type["042a0b44-1cf9-4a0e-9cfb-a0773ec19e26"]
    probe = by_type["MiniMaxH3MotionContextSeamProbe"]
    trim = by_type["MiniMaxH3MotionContextTrim"]
    sampler_audio = next(output for output in sampler["outputs"] if output["name"] == "AUDIO")
    probe_audio_in = next(inp for inp in probe["inputs"] if inp["name"] == "clip_b_untrimmed")
    probe_audio_out = next(output for output in probe["outputs"] if output["name"] == "audio")
    trim_audio = next(inp for inp in trim["inputs"] if inp["name"] == "audio")
    assert probe_audio_in["link"] in sampler_audio["links"]
    assert trim_audio["link"] in probe_audio_out["links"]
    assert any(link[1] == by_type["MiniMaxH3MotionContext"]["id"] and link[3] == probe["id"]
               and probe["inputs"][link[4]]["name"] == "trim_frames" for link in links)
    assert any(link[1] == by_type["MiniMaxH3MotionContextLoadLatent"]["id"] and link[3] == probe["id"]
               and probe["inputs"][link[4]]["name"] == "clip_a_latent" for link in links)
    model = by_type[next(t for t in types if t.count("-") == 4)]
    assert any(link[1] == model["id"] and link[3] == sampler["id"]
               and sampler["inputs"][link[4]]["name"] == "model" for link in links)


def test_named_widgets_and_retained_loader_definition_use_installed_models():
    module_path = ROOT / "scripts/h3_motion_context_workflow.py"
    spec = importlib.util.spec_from_file_location("h3_motion_context_workflow", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.prepare_workflow(json.loads(SOURCE.read_text(encoding="utf-8")))
    definitions = {item["id"]: item for item in result["definitions"]["subgraphs"]}
    loader = next(node for node in result["nodes"] if node["type"] in definitions
                  and any(output["name"] == "MODEL" for output in node.get("outputs", [])))
    chain = next(node for node in result["nodes"] if node["type"] == "MiniMaxH3MotionContextChain")
    assert loader["widgets_values"][:2] == [
        "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    ]
    assert loader["widgets_values_named"]["unet_name"] == loader["widgets_values"][0]
    assert loader["widgets_values_named"]["clip_name"] == loader["widgets_values"][1]
    assert chain["widgets_values_named"]["segments"] == chain["widgets_values"][0] == 2
    image_to_video = next(node for node in result["nodes"] if node["type"] == "MiniMaxH3ImageToVideo")
    context = next(node for node in result["nodes"] if node["type"] == "MiniMaxH3MotionContext")
    assert image_to_video["widgets_values_named"]["length"] == image_to_video["widgets_values"][-1] == 73
    assert context["widgets_values_named"]["context_length"] == context["widgets_values"][0] == "22"
    assert context["widgets_values_named"]["audio_context_length"] == context["widgets_values"][1] == 24
    loader_nodes = {node["type"]: node for node in definitions[loader["type"]]["nodes"]}
    for node_type, key, expected in (
        ("UNETLoader", "unet_name", loader["widgets_values"][0]),
        ("CLIPLoader", "clip_name", loader["widgets_values"][1]),
    ):
        assert loader_nodes[node_type]["widgets_values_named"][key] == expected
        assert loader_nodes[node_type]["widgets_values"][0] == expected


def test_only_referenced_fl2va_subgraphs_remain():
    module_path = ROOT / "scripts/h3_motion_context_workflow.py"
    spec = importlib.util.spec_from_file_location("h3_motion_context_workflow", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.prepare_workflow(json.loads(SOURCE.read_text(encoding="utf-8")))
    definitions = {item["id"]: item for item in result["definitions"]["subgraphs"]}
    for node in [*result["nodes"], *(node for item in definitions.values() for node in item["nodes"])]:
        try:
            UUID(node["type"])
        except ValueError:
            continue
        assert node["type"] in definitions
    referenced = {node["type"] for node in result["nodes"] if node["type"] in definitions}
    for item in definitions.values():
        referenced.update(node["type"] for node in item["nodes"] if node["type"] in definitions)
    assert referenced == set(definitions)
    assert len(definitions) == 2
    assert "ref2va_pruned" not in json.dumps(result).lower()


def test_nested_fl2va_definition_dependency_is_retained():
    module_path = ROOT / "scripts/h3_motion_context_workflow.py"
    spec = importlib.util.spec_from_file_location("h3_motion_context_workflow", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    sampler_definition = next(item for item in source["definitions"]["subgraphs"]
                              if item["id"] == "042a0b44-1cf9-4a0e-9cfb-a0773ec19e26")
    nested_id = "118aa526-b069-47c3-993b-4fb21558740b"
    sampler_definition["nodes"].append({"id": 9999, "type": nested_id})
    source["definitions"]["subgraphs"].append({"id": nested_id, "nodes": []})
    result = module.prepare_workflow(source)
    assert nested_id in {item["id"] for item in result["definitions"]["subgraphs"]}


def test_missing_sampler_definition_fails_before_cli_writes_destination(tmp_path):
    module_path = ROOT / "scripts/h3_motion_context_workflow.py"
    spec = importlib.util.spec_from_file_location("h3_motion_context_workflow", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    missing_id = "042a0b44-1cf9-4a0e-9cfb-a0773ec19e26"
    source["definitions"]["subgraphs"] = [
        item for item in source["definitions"]["subgraphs"] if item["id"] != missing_id
    ]
    try:
        module.prepare_workflow(source)
    except ValueError as exc:
        assert missing_id in str(exc)
    else:
        assert False, "Missing sampler definition must fail closed"

    invalid_source = tmp_path / "invalid.json"
    invalid_source.write_text(json.dumps(source), encoding="utf-8")
    for exists in (False, True):
        destination = tmp_path / f"alias-{exists}.json"
        if exists:
            destination.write_text("existing template", encoding="utf-8")
        run = subprocess.run([sys.executable, str(module_path), str(invalid_source), str(destination)],
                             capture_output=True, text=True)
        assert run.returncode != 0
        assert missing_id in run.stderr
        if exists:
            assert destination.read_text(encoding="utf-8") == "existing template"
        else:
            assert not destination.exists()


def test_probe_links_follow_reordered_output_roles_and_types():
    module_path = ROOT / "scripts/h3_motion_context_workflow.py"
    spec = importlib.util.spec_from_file_location("h3_motion_context_workflow", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    context = next(node for node in source["nodes"] if node["type"] == "MiniMaxH3MotionContext"
                   and node["pos"][1] > 5500)
    context["outputs"].reverse()
    for link in source["links"]:
        if link[1] == context["id"]:
            link[2] = 1 - link[2]
    loader = next(node for node in source["nodes"] if node["type"] == "ad044397-cdc4-4c25-820c-cfb3a9f00383")
    loader["outputs"].reverse()
    for link in source["links"]:
        if link[1] == loader["id"]:
            link[2] = len(loader["outputs"]) - 1 - link[2]
    result = module.prepare_workflow(source)
    nodes = {node["id"]: node for node in result["nodes"]}
    probe = next(node for node in nodes.values() if node["type"] == "MiniMaxH3MotionContextSeamProbe")
    edges = [link for link in result["links"] if link[1] == probe["id"] or link[3] == probe["id"]]
    assert len(edges) == 5
    for link in edges:
        output = nodes[link[1]]["outputs"][link[2]]
        target = nodes[link[3]]["inputs"][link[4]]
        assert output["type"] == target["type"] == link[5]
    trim_edge = next(link for link in edges if link[1] == context["id"]
                     and probe["inputs"][link[4]]["name"] == "trim_frames")
    assert context["outputs"][trim_edge[2]]["name"] == "trim_frames"


def test_probe_rejects_wrong_declared_output_type():
    module_path = ROOT / "scripts/h3_motion_context_workflow.py"
    spec = importlib.util.spec_from_file_location("h3_motion_context_workflow", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    context = next(node for node in source["nodes"] if node["type"] == "MiniMaxH3MotionContext"
                   and node["pos"][1] > 5500)
    next(output for output in context["outputs"] if output["name"] == "trim_frames")["type"] = "CONDITIONING"
    try:
        module.prepare_workflow(source)
    except ValueError as exc:
        assert "trim_frames" in str(exc) and "INT" in str(exc)
    else:
        assert False, "Mismatched Seam Probe output must fail closed"


def test_missing_transitive_subgraph_definition_fails_closed():
    module_path = ROOT / "scripts/h3_motion_context_workflow.py"
    spec = importlib.util.spec_from_file_location("h3_motion_context_workflow", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    sampler_definition = next(item for item in source["definitions"]["subgraphs"]
                              if item["id"] == "042a0b44-1cf9-4a0e-9cfb-a0773ec19e26")
    missing_id = "118aa526-b069-47c3-993b-4fb21558740b"
    sampler_definition["nodes"].append({"id": 9999, "type": missing_id})
    try:
        module.prepare_workflow(source)
    except ValueError as exc:
        assert missing_id in str(exc)
    else:
        assert False, "Missing nested definition must fail closed"

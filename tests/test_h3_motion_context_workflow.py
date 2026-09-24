"""Offline checks for the pinned Motion Context smoke workflow."""

import json
import importlib.util
from pathlib import Path
import subprocess
import sys


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

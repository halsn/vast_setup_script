"""Read-only H3 Motion Context preflight and user-generated segment verification."""

import argparse
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen
from uuid import UUID


SOURCE = "ComfyUI-H3-Motion-Context"
TEMPLATE = "h3_motion_context_smoke"
UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
NODE_TYPES = (
    "MiniMaxH3MotionContext",
    "MiniMaxH3MotionContextTrim",
    "MiniMaxH3MotionContextSaveLatent",
    "MiniMaxH3MotionContextLoadLatent",
    "MiniMaxH3MotionContextChain",
    "MiniMaxH3MotionContextSeamProbe",
)
FPS = 24
DURATION_TOLERANCE_SECONDS = 0.1


class SmokeError(Exception):
    """An expected readiness or media validation failure."""


def _get_json(url):
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=20) as response:
            if response.status != 200:
                raise SmokeError(f"GET {url} returned HTTP {response.status}")
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        raise SmokeError(f"GET {url} failed: {exc}") from exc


def _check_model_options(object_info):
    for node_type, field, expected in (
        ("UNETLoader", "unet_name", UNET),
        ("CLIPLoader", "clip_name", CLIP),
    ):
        node_info = object_info.get(node_type)
        required = (node_info or {}).get("input", {}).get("required", {})
        choice = required.get(field)
        options = choice[0] if isinstance(choice, list) and choice else None
        if not isinstance(options, list) or expected not in options:
            raise SmokeError(f"installed {node_type} option is missing: {expected}")


def _exactly_one(nodes, node_type):
    matches = [node for node in nodes if node.get("type") == node_type]
    if len(matches) != 1:
        raise SmokeError(f"workflow must contain exactly one {node_type}; found {len(matches)}")
    return matches[0]


def _named(items, name, owner):
    matches = [item for item in items if item.get("name") == name]
    if len(matches) != 1:
        raise SmokeError(f"{owner} must have exactly one {name} slot")
    return matches[0]


def _validate_graph(workflow):
    nodes = workflow.get("nodes")
    links = workflow.get("links")
    definitions = workflow.get("definitions", {}).get("subgraphs")
    if not isinstance(nodes, list) or not nodes or not isinstance(links, list):
        raise SmokeError("workflow JSON has no loadable nodes/links graph")
    if not isinstance(definitions, list):
        raise SmokeError("workflow JSON has no subgraph definitions")

    by_id = {node.get("id"): node for node in nodes if isinstance(node, dict)}
    if len(by_id) != len(nodes) or None in by_id:
        raise SmokeError("workflow node IDs are missing or duplicated")
    link_by_id = {}
    for link in links:
        if not isinstance(link, list) or len(link) != 6:
            raise SmokeError("workflow contains a malformed link")
        link_id, source_id, source_slot, target_id, target_slot, link_type = link
        if link_id in link_by_id:
            raise SmokeError(f"workflow link ID is duplicated: {link_id}")
        source = by_id.get(source_id)
        target = by_id.get(target_id)
        if source is None or target is None:
            raise SmokeError(f"workflow link {link_id} has a missing node endpoint")
        outputs = source.get("outputs") or []
        inputs = target.get("inputs") or []
        if not isinstance(source_slot, int) or not 0 <= source_slot < len(outputs):
            raise SmokeError(f"workflow link {link_id} has an invalid output slot")
        if not isinstance(target_slot, int) or not 0 <= target_slot < len(inputs):
            raise SmokeError(f"workflow link {link_id} has an invalid input slot")
        if outputs[source_slot].get("type") != link_type or inputs[target_slot].get("type") != link_type:
            raise SmokeError(f"workflow link {link_id} has incompatible slot types")
        if link_id not in (outputs[source_slot].get("links") or []):
            raise SmokeError(f"workflow output does not list link {link_id}")
        if inputs[target_slot].get("link") != link_id:
            raise SmokeError(f"workflow input does not list link {link_id}")
        link_by_id[link_id] = link

    for node in nodes:
        for index, slot in enumerate(node.get("inputs") or []):
            link_id = slot.get("link")
            if link_id is not None and (
                link_id not in link_by_id
                or link_by_id[link_id][3:5] != [node["id"], index]
            ):
                raise SmokeError(f"workflow input link is inconsistent: {node.get('type')}.{slot.get('name')}")
        for index, slot in enumerate(node.get("outputs") or []):
            for link_id in slot.get("links") or []:
                if link_id not in link_by_id or link_by_id[link_id][1:3] != [node["id"], index]:
                    raise SmokeError(f"workflow output link is inconsistent: {node.get('type')}.{slot.get('name')}")

    definition_by_id = {item.get("id"): item for item in definitions if isinstance(item, dict)}
    if len(definition_by_id) != len(definitions) or None in definition_by_id:
        raise SmokeError("workflow subgraph definitions have missing or duplicate IDs")
    for node in nodes:
        try:
            reference = UUID(str(node.get("type")))
        except (ValueError, TypeError, AttributeError):
            continue
        if str(reference) not in definition_by_id:
            raise SmokeError(f"workflow references missing subgraph definition: {reference}")
    return definition_by_id


def _check_model_values(workflow, definition_by_id):
    candidates = []
    for definition in definition_by_id.values():
        definition_nodes = definition.get("nodes") or []
        unets = [node for node in definition_nodes if node.get("type") == "UNETLoader"]
        clips = [node for node in definition_nodes if node.get("type") == "CLIPLoader"]
        if unets or clips:
            if len(unets) != 1 or len(clips) != 1:
                raise SmokeError("workflow model loader subgraph must contain one UNETLoader and one CLIPLoader")
            parents = [node for node in workflow["nodes"]
                       if node.get("type") == definition.get("id")
                       and any(output.get("name") == "MODEL" for output in node.get("outputs") or [])]
            if len(parents) != 1:
                raise SmokeError("workflow H3 model loader subgraph is missing or duplicated")
            candidates.append((parents[0], unets[0], clips[0]))
    if len(candidates) != 1:
        raise SmokeError(f"workflow must contain one H3 model loader subgraph; found {len(candidates)}")

    model_loader, unet_node, clip_node = candidates[0]
    for node, field, expected in (
        (unet_node, "unet_name", UNET),
        (clip_node, "clip_name", CLIP),
    ):
        widgets = node.get("widgets_values") or []
        named_values = node.get("widgets_values_named") or {}
        if not widgets or widgets[0] != expected or named_values.get(field) != expected:
            raise SmokeError(f"workflow does not select the installed model: {expected}")
    return model_loader


def _check_connection(workflow, source_node, output_name, target_node, input_name, link_type):
    output = _named(source_node.get("outputs") or [], output_name, source_node["type"])
    input_slot = _named(target_node.get("inputs") or [], input_name, target_node["type"])
    if output.get("type") != link_type or input_slot.get("type") != link_type:
        raise SmokeError(f"{source_node['type']}.{output_name} -> {target_node['type']}.{input_name} must be {link_type}")
    link_id = input_slot.get("link")
    if link_id is None or link_id not in (output.get("links") or []):
        raise SmokeError(f"required workflow link is missing: {source_node['type']}.{output_name} -> {target_node['type']}.{input_name}")
    if not any(link[0] == link_id and link[1] == source_node["id"]
               and link[3] == target_node["id"] and link[5] == link_type
               for link in workflow["links"]):
        raise SmokeError(f"required workflow link is inconsistent: {source_node['type']}.{output_name} -> {target_node['type']}.{input_name}")


def _check_smoke_workflow(workflow):
    nodes = workflow.get("nodes")
    if not isinstance(nodes, list):
        raise SmokeError("workflow JSON has no loadable nodes/links graph")
    by_type = {kind: _exactly_one(nodes, kind) for kind in NODE_TYPES}
    definition_by_id = _validate_graph(workflow)
    node_types = [node.get("type") for node in nodes]
    generators = [kind for kind in node_types
                  if isinstance(kind, str) and kind.startswith("MiniMaxH3") and kind.endswith("ToVideo")]
    if generators != ["MiniMaxH3ImageToVideo"]:
        raise SmokeError(f"workflow must run exactly one FL2VA/T2V node; found {generators}")
    fl2va = _exactly_one(nodes, "MiniMaxH3ImageToVideo")
    if (fl2va.get("widgets_values_named") or {}).get("length") != 73 \
            or not fl2va.get("widgets_values") or fl2va["widgets_values"][-1] != 73:
        raise SmokeError("FL2VA/T2V node must be configured for exactly 73 frames")
    if any(kind in node_types for kind in ("LoadImage", "LoadVideo", "LoadAudio", "MiniMaxH3ReferenceToVideo")):
        raise SmokeError("smoke workflow must not require reference media or Ref2VA")

    context = by_type["MiniMaxH3MotionContext"]
    trim = by_type["MiniMaxH3MotionContextTrim"]
    save = by_type["MiniMaxH3MotionContextSaveLatent"]
    load = by_type["MiniMaxH3MotionContextLoadLatent"]
    chain = by_type["MiniMaxH3MotionContextChain"]
    probe = by_type["MiniMaxH3MotionContextSeamProbe"]
    if (chain.get("widgets_values") != [2]
            or (chain.get("widgets_values_named") or {}).get("segments") != 2):
        raise SmokeError("Motion Context Chain must default to 2 segments")
    if ((load.get("widgets_values_named") or {}).get("clip_index") != 0
            or (save.get("widgets_values_named") or {}).get("clip_index") != 1):
        raise SmokeError("smoke workflow must begin with Load 0 / Save 1")
    if not (load.get("widgets_values_named") or {}).get("latent_path"):
        raise SmokeError("Motion Context Load Latent path is empty")
    if not (save.get("widgets_values_named") or {}).get("filename_prefix"):
        raise SmokeError("Motion Context Save Latent filename prefix is empty")
    if ((trim.get("widgets_values_named") or {}).get("fps") != FPS
            or len(trim.get("widgets_values") or []) < 2
            or trim["widgets_values"][1] != FPS):
        raise SmokeError("Motion Context Trim must use 24 fps")

    samplers = [node for node in nodes
                if any(slot.get("name") == "model" for slot in node.get("inputs") or [])
                and any(slot.get("name") == "AUDIO" for slot in node.get("outputs") or [])]
    if len(samplers) != 1:
        raise SmokeError(f"workflow must have one FL2VA sampler; found {len(samplers)}")
    sampler = samplers[0]
    model_loader = _check_model_values(workflow, definition_by_id)

    _check_connection(workflow, fl2va, "positive", context, "conditioning", "CONDITIONING")
    _check_connection(workflow, fl2va, "LATENT", context, "latent", "LATENT")
    _check_connection(workflow, fl2va, "LATENT", sampler, "latent_image", "LATENT")
    _check_connection(workflow, context, "conditioning", sampler, "conditioning", "CONDITIONING")
    _check_connection(workflow, load, "LATENT", context, "context_latent", "LATENT")
    _check_connection(workflow, load, "LATENT", probe, "clip_a_latent", "LATENT")
    _check_connection(workflow, context, "trim_frames", trim, "trim_frames", "INT")
    _check_connection(workflow, context, "trim_frames", probe, "trim_frames", "INT")
    _check_connection(workflow, model_loader, "MODEL", sampler, "model", "MODEL")
    _check_connection(workflow, model_loader, "CLIP", fl2va, "clip", "CLIP")
    _check_connection(workflow, model_loader, "VAE", fl2va, "vae", "VAE")
    _check_connection(workflow, model_loader, "VAE", context, "vae", "VAE")
    _check_connection(workflow, model_loader, "VAE", sampler, "vae", "VAE")
    _check_connection(workflow, model_loader, "VAE_1", context, "audio_vae", "VAE")
    _check_connection(workflow, model_loader, "VAE_1", sampler, "vae_1", "VAE")
    _check_connection(workflow, model_loader, "VAE_1", probe, "audio_vae", "VAE")
    _check_connection(workflow, sampler, "output", save, "latent", "LATENT")
    _check_connection(workflow, sampler, "IMAGE", trim, "images", "IMAGE")
    _check_connection(workflow, trim, "images", sampler, "images", "IMAGE")
    _check_connection(workflow, sampler, "AUDIO", probe, "clip_b_untrimmed", "AUDIO")
    _check_connection(workflow, probe, "audio", trim, "audio", "AUDIO")
    _check_connection(workflow, trim, "audio", sampler, "audio", "AUDIO")


def preflight(comfy_url):
    parsed = urlsplit(comfy_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SmokeError("--comfy-url must be an HTTP or HTTPS URL")
    base = comfy_url.rstrip("/")
    object_info = _get_json(base + "/object_info")
    if not isinstance(object_info, dict):
        raise SmokeError("ComfyUI /object_info response is not an object")
    missing_nodes = [kind for kind in NODE_TYPES if kind not in object_info]
    if missing_nodes:
        raise SmokeError("Motion Context nodes are not registered: " + ", ".join(missing_nodes))
    _check_model_options(object_info)

    templates = _get_json(base + "/workflow_templates")
    if not isinstance(templates, dict) or TEMPLATE not in (templates.get(SOURCE) or []):
        raise SmokeError(f"workflow template {TEMPLATE!r} is not listed under {SOURCE!r}")
    workflow_url = (base + "/api/workflow_templates/" + quote(SOURCE, safe="") + "/"
                    + quote(TEMPLATE + ".json", safe=""))
    workflow = _get_json(workflow_url)
    if not isinstance(workflow, dict):
        raise SmokeError("workflow template response is not a JSON object")
    _check_smoke_workflow(workflow)
    print("GET-only preflight passed: registered nodes, installed model options, and workflow JSON structure were verified.")
    print("This check does not exercise the upstream first-use runtime layout contract.")


def verify_latent(path):
    latent = Path(path)
    if not latent.is_file():
        raise SmokeError(f"paired latent file is absent: {latent}")
    if latent.stat().st_size == 0:
        raise SmokeError(f"paired latent file is empty: {latent}")
    print(f"Non-empty paired latent is present: {latent}")


def _probe(path):
    command = [
        "ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                shell=False, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeError(f"ffprobe could not inspect {path}: {exc}") from exc
    if result.returncode != 0:
        raise SmokeError(f"ffprobe failed for {path}: {result.stderr.strip()}")
    try:
        report = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SmokeError(f"ffprobe returned invalid JSON for {path}") from exc
    if not isinstance(report, dict):
        raise SmokeError(f"ffprobe returned invalid media metadata for {path}")
    return report


def _fps(value, path):
    try:
        rate = float(Fraction(str(value)))
    except (ValueError, ZeroDivisionError, TypeError):
        raise SmokeError(f"could not read frame rate for {path}") from None
    if not math.isclose(rate, FPS, rel_tol=0.0, abs_tol=0.01):
        raise SmokeError(f"{path} frame rate is {rate:g} fps; expected 24 fps")


def _duration(value, expected, path):
    try:
        actual = float(value)
    except (ValueError, TypeError):
        raise SmokeError(f"could not read duration for {path}") from None
    if not math.isfinite(actual) or abs(actual - expected) > DURATION_TOLERANCE_SECONDS:
        raise SmokeError(f"{path} duration is {actual:g}s; expected about {expected:.4f}s")


def _validate_media(path, expected_frames):
    report = _probe(path)
    streams = report.get("streams")
    if not isinstance(streams, list):
        raise SmokeError(f"ffprobe did not report streams for {path}")
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if not videos:
        raise SmokeError(f"{path} has no video stream")
    if not audios:
        raise SmokeError(f"{path} has no audio stream")
    if len(videos) != 1:
        raise SmokeError(f"{path} must contain exactly one video stream")
    video = videos[0]
    try:
        frame_count = int(video["nb_read_frames"])
    except (KeyError, TypeError, ValueError):
        raise SmokeError(f"ffprobe did not count video frames for {path}") from None
    if frame_count != expected_frames:
        raise SmokeError(f"{path} has {frame_count} video frames; expected {expected_frames}")
    _fps(video.get("avg_frame_rate"), path)
    _fps(video.get("r_frame_rate"), path)
    expected_duration = expected_frames / FPS
    _duration(video.get("duration"), expected_duration, path)
    for audio in audios:
        _duration(audio.get("duration"), expected_duration, path)
    _duration((report.get("format") or {}).get("duration"), expected_duration, path)


def _run_ffmpeg(command, purpose):
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False,
                                shell=False, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeError(f"ffmpeg {purpose} failed: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip()
        raise SmokeError(f"ffmpeg {purpose} failed" + (f": {detail}" if detail else ""))


def join_verify(first, second, latent, output):
    first_path, second_path = Path(first), Path(second)
    latent_path, output_path = Path(latent), Path(output)
    for label, path in (("first", first_path), ("second", second_path)):
        if not path.is_file():
            raise SmokeError(f"{label} input is absent: {path}")
        if path.stat().st_size == 0:
            raise SmokeError(f"{label} input is empty: {path}")
    verify_latent(latent_path)
    if os.path.lexists(output_path):
        raise SmokeError(f"output already exists; refusing to overwrite: {output_path}")

    _validate_media(first_path, 73)
    _validate_media(second_path, 51)
    command = [
        "ffmpeg", "-v", "error", "-xerror", "-nostdin", "-n",
        "-i", str(first_path), "-i", str(second_path),
        "-filter_complex",
        "[0:v:0][0:a:0][1:v:0][1:a:0]concat=n=2:v=1:a=1[v][a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-r", "24",
        "-fps_mode", "cfr", "-c:a", "aac", str(output_path),
    ]
    _run_ffmpeg(command, "join")
    if not output_path.is_file() or output_path.stat().st_size == 0:
        raise SmokeError(f"ffmpeg did not create a non-empty output: {output_path}")
    _validate_media(output_path, 124)

    decode = ["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(output_path),
              "-map", "0", "-f", "null", "-"]
    _run_ffmpeg(decode, "full decode")
    print(f"Join verified: 73 + 51 = 124 frames at 24 fps, about 5.1667 seconds, with audio and video: {output_path}")


def _parser():
    manual = """Manual restart/resume protocol (the tool never runs these steps):
Chain defaults to 2; setting Chain to 0 is unbounded.
Do not use Chain for the restart test.
1. Make one Run/Re-roll at Load 0 / Save 1 to generate segment 1.
2. Run verify-latent --path FILE on its saved paired latent.
3. Then restart ComfyUI.
4. Make one Run/Re-roll at Load 1 / Save 2 to generate segment 2.
5. Run join-verify to validate and join the two already-trimmed segments.
6. Perform the human visual/audio seam check.
The restart/resume render is manual and is not claimed to have been run."""
    parser = argparse.ArgumentParser(
        description=__doc__, epilog=manual,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    preflight_parser = commands.add_parser("preflight", help="GET-only ComfyUI readiness check")
    preflight_parser.add_argument("--comfy-url", required=True)
    latent_parser = commands.add_parser("verify-latent", help="check one explicit paired-latent file")
    latent_parser.add_argument("--path", required=True)
    join_parser = commands.add_parser("join-verify", help="join and fully verify two generated segments")
    join_parser.add_argument("--first", required=True)
    join_parser.add_argument("--second", required=True)
    join_parser.add_argument("--latent", required=True)
    join_parser.add_argument("--output", required=True)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == "preflight":
            preflight(args.comfy_url)
        elif args.command == "verify-latent":
            verify_latent(args.path)
        else:
            join_verify(args.first, args.second, args.latent, args.output)
    except SmokeError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

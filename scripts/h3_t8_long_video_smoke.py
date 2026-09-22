#!/usr/bin/env python3
"""Real GPU smoke for the pinned T8 H3 Long Video + Prompt Relay + EAV route.

Default mode is preflight-only. Pass --execute to spend GPU time. The executable
path deliberately interrupts after the first accepted segment, verifies the
persisted manifest/media, requeues the identical chain, and proves native resume
to a two-segment 8-second final video.

This is a mechanical release gate, not a human quality rating.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable

FPS = 24
TOTAL_FRAMES = 192
WIDTH = 512
HEIGHT = 288
RENDER_WINDOW_FRAMES = 124
CONTEXT_FRAMES = 22
STATE_FOLDER = "minimax_h3_t8_long_video"
STATE_NAME = "in_node_loop_effects_state.json"
MANIFEST_NAME = "manifest.json"
REPORT_NAME = "last_execution_report.json"
UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
LOOP_NODE = "MiniMaxH3LongVideoInNodeLoopEffectsT8Advanced"
RELAY_NODE = "MiniMaxH3PromptRelayPlanT8Advanced"
GLOBAL_PROMPT = (
    "A single continuous cinematic studio shot of the same glossy red cube on a "
    "neutral table. Preserve the cube, lighting, camera direction, room tone and "
    "spatial continuity across the entire shot. No cuts, no text, no people."
)
LOCAL_PROMPTS = (
    "The red cube slowly rotates clockwise while the camera stays fixed.\n"
    "The cube slides smoothly to the right while continuing the same rotation.\n"
    "The camera slowly pulls back while the cube settles and remains clearly visible."
)


class HttpResult:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = int(status)
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def _url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def request(
    url: str,
    *,
    method: str = "GET",
    payload: Any | None = None,
    timeout: float = 30,
) -> HttpResult:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return HttpResult(response.status, response.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:2000]
        raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc}") from exc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Expected JSON object in {path}")
    return payload


def discover_comfy_root(explicit: str | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("H3_COMFY_DIR"):
        candidates.append(Path(os.environ["H3_COMFY_DIR"]))
    candidates.extend(
        Path(value)
        for value in (
            "/opt/workspace-internal/ComfyUI",
            "/workspace/ComfyUI",
            "/workspace/comfyui",
            "/opt/ComfyUI",
            "/root/ComfyUI",
            "/app/ComfyUI",
            "/mnt/ComfyUI",
        )
    )
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if (resolved / "main.py").is_file():
            return resolved
    raise RuntimeError(
        "Could not locate ComfyUI. Pass --comfy-root or set H3_COMFY_DIR."
    )


def build_prompt(chain_id: str, seed: int) -> dict[str, Any]:
    if not chain_id or len(chain_id) > 96 or any(
        not (char.isalnum() or char in "_-") for char in chain_id
    ):
        raise ValueError("chain_id must use only letters, digits, '_' or '-' and be <=96 chars")
    if not 0 <= int(seed) <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("seed must be between 0 and 2^64-1")

    return {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {"unet_name": UNET, "weight_dtype": "default"},
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": CLIP, "type": "minimax", "device": "default"},
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": VIDEO_VAE},
        },
        "4": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": AUDIO_VAE},
        },
        "5": {
            "class_type": RELAY_NODE,
            "inputs": {
                "global_prompt": GLOBAL_PROMPT,
                "local_prompts": LOCAL_PROMPTS,
                "length": TOTAL_FRAMES,
                "timing_mode": "frames",
                "time_ranges": "0-63\n64-127\n128-191",
                "math_profile": "paper_v1",
                "epsilon": 0.1,
                "allow_gaps": False,
                "allow_overlaps": False,
            },
        },
        "6": {
            "class_type": LOOP_NODE,
            "inputs": {
                "model": ["1", 0],
                "clip": ["2", 0],
                "video_vae": ["3", 0],
                "audio_vae": ["4", 0],
                "chain_id": chain_id,
                "total_duration_seconds": 8.0,
                "width": WIDTH,
                "height": HEIGHT,
                "render_window_frames": RENDER_WINDOW_FRAMES,
                "context_frames": CONTEXT_FRAMES,
                "global_prompt": "",
                "segment_prompts_json": "",
                "prompt_relay_mode": "apply_exp",
                "query_chunk_rows": 256,
                "eav_mode": "apply_exp",
                "eav_tau": 4.0,
                "eav_start_video_progress": 0.0,
                "eav_end_video_progress": 1.0,
                "eav_max_workspace_mib": 32,
                "eav_g_hard_limit": 1.5,
                "minimum_free_vram_mib": 512,
                "base_seed": int(seed),
                "seed_policy": "increment",
                "steps": 20,
                "shift_video": 12.0,
                "shift_audio": 3.0,
                "sampler_name": "dual_clock_euler",
                "scheduler": "native_flow",
                "task_type": "T2VA",
                "context_audio": "video_and_audio",
                "audio_mode": "native",
                "audio_denoise_strength": 0.35,
                "add_source_as_reference": False,
                "prompt_primary_audio_ordinal": 0,
                "strict_prompt_tags": True,
                "ref_image_size": "match",
                "reference_video_policy": "official_2_to_15s",
                "first_frame_reuse": "segment0_only",
                "persistent_identity_strategy": "single_reference",
                "persistent_identity_interval": 1,
                "resume_existing": True,
                "filename_prefix": "H3_T8_Relay_EAV_Release_Smoke_8s",
                "audio_seam_policy": "cosine_bridge",
                "bridge_ms": 5.0,
                "bit_depth": 8,
                "crf": 18,
                "model_id": "minimax_h3_native_stock20_relay_eav_release_smoke",
                "prompt_relay_plan": ["5", 0],
            },
        },
    }


def validate_object_catalog(catalog: dict[str, Any]) -> None:
    required = {
        "UNETLoader",
        "CLIPLoader",
        "VAELoader",
        RELAY_NODE,
        LOOP_NODE,
    }
    missing = sorted(required - set(catalog))
    if missing:
        raise RuntimeError("Required T8 smoke nodes are missing: " + ", ".join(missing))

    def combo(node: str, name: str) -> list[str]:
        try:
            value = catalog[node]["input"]["required"][name][0]
        except (KeyError, IndexError, TypeError):
            return []
        return value if isinstance(value, list) else []

    checks = (
        ("UNETLoader", "unet_name", UNET),
        ("CLIPLoader", "clip_name", CLIP),
        ("VAELoader", "vae_name", VIDEO_VAE),
        ("VAELoader", "vae_name", AUDIO_VAE),
    )
    for node, field, expected in checks:
        if expected not in combo(node, field):
            raise RuntimeError(f"{expected} is not selectable in {node}.{field}")


def assert_queue_idle(comfy_url: str) -> None:
    queue = request(_url(comfy_url, "/queue")).json()
    running = queue.get("queue_running") or []
    pending = queue.get("queue_pending") or []
    if running or pending:
        raise RuntimeError(
            "ComfyUI queue is not idle; refusing to run a destructive interrupt smoke"
        )


def submit_prompt(comfy_url: str, graph: dict[str, Any]) -> str:
    result = request(
        _url(comfy_url, "/prompt"),
        method="POST",
        payload={"prompt": graph, "client_id": uuid.uuid4().hex},
        timeout=60,
    ).json()
    prompt_id = result.get("prompt_id")
    if not isinstance(prompt_id, str) or not prompt_id:
        raise RuntimeError(f"ComfyUI did not return prompt_id: {result}")
    return prompt_id


def _queue_prompt_ids(queue: dict[str, Any], key: str) -> list[str]:
    result: list[str] = []
    for item in queue.get(key) or []:
        if isinstance(item, list) and len(item) > 1 and isinstance(item[1], str):
            result.append(item[1])
        elif isinstance(item, dict) and isinstance(item.get("prompt_id"), str):
            result.append(item["prompt_id"])
    return result


def interrupt_owned_prompt(comfy_url: str, prompt_id: str) -> None:
    queue = request(_url(comfy_url, "/queue")).json()
    running = _queue_prompt_ids(queue, "queue_running")
    if running != [prompt_id]:
        raise RuntimeError(
            f"Refusing interrupt: running queue is {running!r}, expected only {prompt_id!r}"
        )
    response = request(
        _url(comfy_url, "/interrupt"),
        method="POST",
        payload={"prompt_id": prompt_id},
        timeout=20,
    )
    if response.status != 200:
        raise RuntimeError(f"Interrupt returned HTTP {response.status}")


def wait_for(
    predicate: Callable[[], Any],
    *,
    timeout: float,
    description: str,
    interval: float = 0.25,
) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
        time.sleep(interval)
    detail = f"; last error: {last_error}" if last_error else ""
    raise TimeoutError(f"Timed out waiting for {description}{detail}")


def wait_history(comfy_url: str, prompt_id: str, timeout: float) -> dict[str, Any]:
    def terminal() -> dict[str, Any] | None:
        payload = request(_url(comfy_url, f"/history/{prompt_id}"), timeout=20).json()
        record = payload.get(prompt_id)
        if not isinstance(record, dict):
            return None
        status = record.get("status") or {}
        messages = status.get("messages") or []
        terminal_kinds = {
            item[0]
            for item in messages
            if isinstance(item, list) and len(item) == 2 and isinstance(item[0], str)
        }
        if status.get("completed") is True or terminal_kinds & {
            "execution_success",
            "execution_error",
            "execution_interrupted",
        }:
            return record
        return None

    return wait_for(
        terminal,
        timeout=timeout,
        description=f"ComfyUI history for {prompt_id}",
        interval=1.0,
    )


def history_terminal_kind(record: dict[str, Any]) -> str:
    messages = (record.get("status") or {}).get("messages") or []
    kinds = [
        item[0]
        for item in messages
        if isinstance(item, list)
        and len(item) == 2
        and item[0] in {"execution_success", "execution_error", "execution_interrupted"}
    ]
    if kinds:
        return str(kinds[-1])
    if (record.get("status") or {}).get("completed") is True:
        return "execution_success"
    return "unknown"


def chain_root(comfy_root: Path, chain_id: str) -> Path:
    root = (comfy_root / "output" / STATE_FOLDER / chain_id).resolve()
    output = (comfy_root / "output").resolve()
    if output not in root.parents:
        raise RuntimeError("Resolved chain path escaped ComfyUI output")
    return root


def _inside(root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise RuntimeError(f"Path escaped chain root: {value}")
    return resolved


def validate_state(
    state: dict[str, Any],
    *,
    chain_id: str,
    status: str,
    accepted_count: int,
) -> None:
    expected = {
        "schema": 1,
        "format": "minimax_h3_t8_in_node_loop_effects",
        "chain_id": chain_id,
        "status": status,
        "segment_count": 2,
        "accepted_count": accepted_count,
        "manifest_revision": accepted_count,
        "current_segment_index": None if accepted_count == 2 else accepted_count,
    }
    mismatches = [
        key for key, value in expected.items() if state.get(key) != value
    ]
    if mismatches:
        raise RuntimeError(
            "T8 state mismatch (" + ", ".join(mismatches) + "): " + repr(state)
        )
    contract = state.get("contract_sha256")
    if not isinstance(contract, str) or len(contract) != 64:
        raise RuntimeError("T8 state is missing a 64-char contract SHA")


def validate_manifest(manifest: dict[str, Any], chain_id: str, count: int) -> None:
    if (
        manifest.get("schema") != 2
        or manifest.get("format") != "minimax_h3_t8_accepted_manifest"
        or manifest.get("chain_id") != chain_id
        or manifest.get("revision") != count
    ):
        raise RuntimeError("T8 accepted manifest header is invalid")
    segments = manifest.get("segments")
    if not isinstance(segments, list) or len(segments) != count:
        raise RuntimeError(f"T8 accepted manifest expected {count} segments")
    for index, entry in enumerate(segments):
        if entry.get("index") != index or entry.get("fps") != FPS:
            raise RuntimeError(f"Manifest segment {index} identity/fps is invalid")
        if entry.get("width") != WIDTH or entry.get("height") != HEIGHT:
            raise RuntimeError(f"Manifest segment {index} geometry is invalid")
    if count >= 1 and segments[0].get("frame_count") != RENDER_WINDOW_FRAMES:
        raise RuntimeError("First segment is not the expected 124 frames")
    if count == 2:
        if segments[1].get("frame_count") != TOTAL_FRAMES - RENDER_WINDOW_FRAMES:
            raise RuntimeError("Final segment effective frame count is not 68")
        if segments[0].get("is_final_segment") is not False:
            raise RuntimeError("First segment was incorrectly marked final")
        if segments[1].get("is_final_segment") is not True:
            raise RuntimeError("Second segment was not marked final")


def capture_first_evidence(root: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    validate_manifest(manifest, str(manifest.get("chain_id")), 1)
    entry = copy.deepcopy(manifest["segments"][0])
    candidate_id = entry.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise RuntimeError("First accepted segment has no candidate_id")
    relative_files = {
        "video": entry.get("video_path"),
        "context": entry.get("context_path"),
        "candidate": f"candidates/segment_00000/{candidate_id}/candidate.json",
        "audit": f"candidates/segment_00000/{candidate_id}/effects_audit.json",
    }
    identities: dict[str, Any] = {}
    for name, value in relative_files.items():
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"First accepted segment is missing {name} path")
        path = _inside(root, value)
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"First accepted {name} file is missing or unsafe: {path}")
        identities[name] = {
            "path": str(path.relative_to(root)),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
    if identities["video"]["sha256"] != entry.get("video_sha256"):
        raise RuntimeError("First accepted video checksum does not match manifest")
    if identities["context"]["sha256"] != entry.get("context_sha256"):
        raise RuntimeError("First accepted context checksum does not match manifest")
    audit = read_json(_inside(root, relative_files["audit"]))
    if (audit.get("prompt_relay") or {}).get("status") != "applied_exp":
        raise RuntimeError("First accepted segment did not apply Prompt Relay")
    if (audit.get("enhance_a_video_audit") or {}).get("status") != "verified":
        raise RuntimeError("First accepted segment EAV audit is not verified")
    return {"entry": entry, "files": identities}


def verify_first_unchanged(root: Path, frozen: dict[str, Any]) -> None:
    manifest = read_json(root / MANIFEST_NAME)
    if not manifest.get("segments") or manifest["segments"][0] != frozen["entry"]:
        raise RuntimeError("Resume changed the first accepted manifest entry")
    for identity in frozen["files"].values():
        path = _inside(root, identity["path"])
        observed = {
            "path": identity["path"],
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        if observed != identity:
            raise RuntimeError(f"Resume changed persisted first-segment file: {path}")


def validate_final_report(root: Path) -> dict[str, Any]:
    report = read_json(root / REPORT_NAME)
    if report.get("status") != "complete" or report.get("accepted_count") != 2:
        raise RuntimeError("T8 final execution report is not complete/two-segment")
    if report.get("resume_action") not in {
        "generated_or_resumed_then_completed",
        "adopted_existing_then_completed",
    }:
        raise RuntimeError("T8 final report does not describe a resume-capable completion")
    effects = report.get("effect_contract") or {}
    if effects.get("prompt_relay_mode") != "apply_exp" or effects.get("eav_mode") != "apply_exp":
        raise RuntimeError("T8 final report lost Prompt Relay or EAV")
    audits = report.get("segment_audits")
    if not isinstance(audits, list) or len(audits) != 2:
        raise RuntimeError("T8 final report does not contain two segment audits")
    for index, audit in enumerate(audits):
        if (audit.get("prompt_relay") or {}).get("status") != "applied_exp":
            raise RuntimeError(f"Segment {index} Prompt Relay was not applied")
        if (audit.get("enhance_a_video_audit") or {}).get("status") != "verified":
            raise RuntimeError(f"Segment {index} EAV audit was not verified")
    return report


def verify_final_media(root: Path, state: dict[str, Any], evidence_dir: Path) -> dict[str, Any]:
    value = state.get("final_video_path")
    if not isinstance(value, str) or not value:
        raise RuntimeError("Complete state has no final_video_path")
    path = _inside(root, value)
    if not path.is_file() or path.stat().st_size < 1024:
        raise RuntimeError(f"Final video is missing or unexpectedly small: {path}")
    digest = sha256_file(path)
    if digest != state.get("final_video_sha256"):
        raise RuntimeError("Final video checksum does not match complete state")

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required for the T8 GPU release smoke")
    probe = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-count_frames",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "ffprobe.json").write_text(
        json.dumps(
            {
                "returncode": probe.returncode,
                "stdout": probe.stdout,
                "stderr": probe.stderr,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if probe.returncode != 0:
        raise RuntimeError("ffprobe failed for final T8 smoke video")
    payload = json.loads(probe.stdout)
    videos = [s for s in payload.get("streams", []) if s.get("codec_type") == "video"]
    audios = [s for s in payload.get("streams", []) if s.get("codec_type") == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise RuntimeError("Final T8 smoke video must contain one video and one audio stream")
    video = videos[0]
    if (video.get("width"), video.get("height")) != (WIDTH, HEIGHT):
        raise RuntimeError("Final T8 smoke video geometry mismatch")
    if int(video.get("nb_read_frames", -1)) != TOTAL_FRAMES:
        raise RuntimeError("Final T8 smoke video is not exactly 192 frames")
    if Fraction(video.get("avg_frame_rate", "0/1")) != FPS:
        raise RuntimeError("Final T8 smoke video is not 24fps")
    duration = float((payload.get("format") or {}).get("duration", 0.0))
    if abs(duration - 8.0) > 1.0 / FPS + 0.05:
        raise RuntimeError(f"Final T8 smoke duration is not ~8s: {duration}")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for strict final media decode")
    decode = subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    (evidence_dir / "strict-decode.txt").write_text(
        f"returncode={decode.returncode}\nstdout:\n{decode.stdout}\nstderr:\n{decode.stderr}\n",
        encoding="utf-8",
    )
    if decode.returncode != 0 or decode.stderr.strip():
        raise RuntimeError("Strict ffmpeg decode failed for final T8 smoke video")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": digest,
        "frames": TOTAL_FRAMES,
        "fps": FPS,
        "width": WIDTH,
        "height": HEIGHT,
        "audio_streams": 1,
        "duration_seconds": duration,
    }


def run_smoke(
    *,
    comfy_url: str,
    comfy_root: Path,
    chain_id: str,
    seed: int,
    timeout: float,
    evidence_dir: Path,
) -> dict[str, Any]:
    assert_queue_idle(comfy_url)
    root = chain_root(comfy_root, chain_id)
    if root.exists():
        raise RuntimeError(
            f"Chain already exists: {root}. Use a new --chain-id to avoid reusing evidence."
        )

    catalog = request(_url(comfy_url, "/object_info"), timeout=60).json()
    validate_object_catalog(catalog)
    graph = build_prompt(chain_id, seed)
    evidence_dir.mkdir(parents=True, exist_ok=False)
    (evidence_dir / "prompt.json").write_text(
        json.dumps(graph, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("[GPU 1/6] submit 8s two-segment T8 Relay+EAV chain")
    first_prompt = submit_prompt(comfy_url, graph)
    (evidence_dir / "first-submission.json").write_text(
        json.dumps({"prompt_id": first_prompt}, indent=2) + "\n",
        encoding="utf-8",
    )

    state_path = root / STATE_NAME
    manifest_path = root / MANIFEST_NAME

    def first_accepted() -> dict[str, Any] | None:
        if not state_path.is_file():
            return None
        state = read_json(state_path)
        accepted = int(state.get("accepted_count", 0))
        if accepted >= 2:
            raise RuntimeError("Missed the one-segment interrupt window")
        if accepted == 1 and state.get("status") == "running":
            validate_state(
                state,
                chain_id=chain_id,
                status="running",
                accepted_count=1,
            )
            return state
        if state.get("status") in {"failed", "waiting_resources"}:
            raise RuntimeError(f"T8 chain failed before interrupt: {state}")
        return None

    print("[GPU 2/6] wait for first accepted segment")
    trigger_state = wait_for(
        first_accepted,
        timeout=timeout,
        description="first accepted T8 segment",
    )
    manifest = read_json(manifest_path)
    validate_manifest(manifest, chain_id, 1)
    frozen = capture_first_evidence(root, manifest)
    (evidence_dir / "first-accepted.json").write_text(
        json.dumps({"state": trigger_state, "evidence": frozen}, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    print("[GPU 3/6] interrupt only the owned running prompt")
    interrupt_owned_prompt(comfy_url, first_prompt)
    first_history = wait_history(comfy_url, first_prompt, timeout)
    first_terminal = history_terminal_kind(first_history)
    if first_terminal != "execution_interrupted":
        raise RuntimeError(
            f"Expected execution_interrupted for first run, got {first_terminal}"
        )
    interrupted = wait_for(
        lambda: (
            read_json(state_path)
            if state_path.is_file()
            and read_json(state_path).get("status") == "interrupted"
            else None
        ),
        timeout=60,
        description="persisted interrupted T8 state",
    )
    validate_state(
        interrupted,
        chain_id=chain_id,
        status="interrupted",
        accepted_count=1,
    )
    verify_first_unchanged(root, frozen)

    print("[GPU 4/6] requeue identical chain for native resume")
    assert_queue_idle(comfy_url)
    second_prompt = submit_prompt(comfy_url, graph)
    (evidence_dir / "resume-submission.json").write_text(
        json.dumps({"prompt_id": second_prompt}, indent=2) + "\n",
        encoding="utf-8",
    )
    second_history = wait_history(comfy_url, second_prompt, timeout)
    second_terminal = history_terminal_kind(second_history)
    if second_terminal != "execution_success":
        raise RuntimeError(f"Resume did not succeed: {second_terminal}")

    print("[GPU 5/6] validate two-segment manifest, audits and immutable first segment")
    complete = wait_for(
        lambda: (
            read_json(state_path)
            if state_path.is_file() and read_json(state_path).get("status") == "complete"
            else None
        ),
        timeout=60,
        description="complete T8 state",
    )
    validate_state(
        complete,
        chain_id=chain_id,
        status="complete",
        accepted_count=2,
    )
    final_manifest = read_json(manifest_path)
    validate_manifest(final_manifest, chain_id, 2)
    verify_first_unchanged(root, frozen)
    report = validate_final_report(root)

    print("[GPU 6/6] strict-decode final 192-frame AV output")
    media = verify_final_media(root, complete, evidence_dir)
    result = {
        "status": "passed",
        "qualification": (
            "mechanical two-segment Stock20 Prompt Relay + EAV interrupt/resume smoke; "
            "human seam/audio/semantic quality review remains separate"
        ),
        "chain_id": chain_id,
        "first_prompt_id": first_prompt,
        "resume_prompt_id": second_prompt,
        "contract_sha256": complete["contract_sha256"],
        "manifest_revision": final_manifest["revision"],
        "accepted_segments": len(final_manifest["segments"]),
        "first_segment_unchanged_after_resume": True,
        "prompt_relay_applied_all_segments": all(
            (item.get("prompt_relay") or {}).get("status") == "applied_exp"
            for item in report["segment_audits"]
        ),
        "eav_verified_all_segments": all(
            (item.get("enhance_a_video_audit") or {}).get("status") == "verified"
            for item in report["segment_audits"]
        ),
        "media": media,
    }
    (evidence_dir / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--comfy-url",
        default="http://127.0.0.1:18188",
        help="ComfyUI base URL",
    )
    parser.add_argument(
        "--comfy-root",
        help="ComfyUI checkout/root. Auto-detected on the Vast image when omitted.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="actually submit GPU work, interrupt after segment 1, then resume",
    )
    parser.add_argument(
        "--chain-id",
        help="unique chain id; default is generated automatically",
    )
    parser.add_argument("--seed", type=int, default=20260922)
    parser.add_argument("--timeout", type=int, default=2400)
    parser.add_argument(
        "--evidence-dir",
        help="directory for prompt/submission/media evidence; default is /tmp/<chain>-evidence",
    )
    args = parser.parse_args(argv)
    if args.timeout < 60:
        parser.error("--timeout must be >= 60")
    if not 0 <= args.seed <= 0xFFFFFFFFFFFFFFFF:
        parser.error("--seed must be between 0 and 2^64-1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    comfy_root = discover_comfy_root(args.comfy_root)
    catalog = request(_url(args.comfy_url, "/object_info"), timeout=60).json()
    validate_object_catalog(catalog)
    print(f"[OK] ComfyUI root: {comfy_root}")
    print("[OK] T8 Long Video + Prompt Relay + EAV nodes/models are registered")

    if not args.execute:
        print(
            "[INFO] GPU work skipped. Re-run with --execute for the paid 8s "
            "interrupt/resume release smoke."
        )
        return 0

    chain_id = args.chain_id or ("h3_t8_release_" + uuid.uuid4().hex[:16])
    evidence_dir = (
        Path(args.evidence_dir).resolve()
        if args.evidence_dir
        else Path("/tmp") / f"{chain_id}-evidence"
    )
    print(
        "[WARN] --execute submits real Stock20 GPU work and intentionally interrupts "
        "the sole running ComfyUI prompt once.",
        file=sys.stderr,
    )
    result = run_smoke(
        comfy_url=args.comfy_url,
        comfy_root=comfy_root,
        chain_id=chain_id,
        seed=args.seed,
        timeout=args.timeout,
        evidence_dir=evidence_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[OK] evidence: {evidence_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

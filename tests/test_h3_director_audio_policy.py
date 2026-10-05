"""CPU-only contract tests for Timeline Director audio output policy."""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/timeline-director/two-phase-checkpoints.patch"
PATCH_TOOL = ROOT / "scripts/h3_timeline_director_patch.py"
DIRECTOR_FILE = "minimax_h3_timeline_director.py"
SAMPLER_FILE = "minimax_h3_finite_segments.py"


def _section(path: str) -> str:
    text = PATCH.read_text(encoding="utf-8")
    marker = f"diff --git a/{path} b/{path}\n"
    return text.split(marker, 1)[1].split("diff --git ", 1)[0]


def _added_function(path: str, name: str) -> str:
    lines = _section(path).splitlines()
    start = next(
        index for index, line in enumerate(lines)
        if line.startswith(f"+def {name}(")
    )
    source = []
    for line in lines[start:]:
        if not line.startswith("+"):
            break
        body = line[1:]
        if source and body.startswith(("def ", "class ", "@")):
            break
        source.append(body)
    return "\n".join(source) + "\n"


def _load_patch_function(path: str, name: str, namespace: dict):
    source = _added_function(path, name)
    exec(compile(source, f"managed_patch:{path}:{name}", "exec"), namespace)
    return namespace[name]


def _finite_plan(video_audio_enabled=None, *, output_mode=None, has_video_audio=True, standalone=False):
    timeline = {
        "videoClips": [{"file": "reference.mp4", "duration": 5.0, "hasAudio": has_video_audio}],
        "audios": ([{"file": "voice.wav", "mode": "locked"}] if standalone else []),
    }
    if video_audio_enabled is not None:
        timeline["videoAudioEnabled"] = video_audio_enabled
    if output_mode is not None:
        timeline["audioOutputMode"] = output_mode
    return {"timeline": timeline}


def _policy_functions(plan):
    finite = {"segment_count": 1, "plans": [plan]}

    def finite_plan_for_segment(value, number):
        return value["plans"][number - 1]

    def locked_audio_for_plan(value, *, include_video_soundtrack):
        timeline = value["timeline"]
        standalone = next(
            (asset for asset in timeline.get("audios", []) if asset.get("mode") == "locked"),
            None,
        )
        if standalone is not None:
            return standalone
        clip = next(iter(timeline.get("videoClips", [])), {})
        if include_video_soundtrack and clip.get("hasAudio", True) and clip.get("duration", 0) > 0:
            return {"lockKind": "timeline_video_audio"}
        return None

    namespace = {
        "_finite_plan_for_segment": finite_plan_for_segment,
        "_require_timeline_plan": lambda value: value,
        "_normalize_timeline_audio_output_mode": lambda timeline: (
            timeline.get("audioOutputMode")
            if timeline.get("audioOutputMode") in {"source", "generate", "silent"}
            else ("generate" if timeline.get("videoAudioEnabled", True) is False else "source")
        ),
        "_finite_locked_audio_asset": lambda value, *, include_video_soundtrack: (
            locked_audio_for_plan(finite_plan_for_segment(value, 1), include_video_soundtrack=include_video_soundtrack)
        ),
    }
    _load_patch_function(SAMPLER_FILE, "_resolve_audio_output_mode", namespace)
    _load_patch_function(SAMPLER_FILE, "_finite_audio_output_policy", namespace)
    _load_patch_function(SAMPLER_FILE, "_finite_audio_latent_graph", namespace)
    _load_patch_function(SAMPLER_FILE, "_finite_audio_master_graph", namespace)
    return finite, namespace


def test_timeline_audio_mode_is_validated_and_controls_reference_audio_decode():
    namespace = {}
    _load_patch_function(DIRECTOR_FILE, "_normalize_timeline_audio_output_mode", namespace)
    _load_patch_function(DIRECTOR_FILE, "_timeline_video_audio_enabled", namespace)
    normalize = namespace["_normalize_timeline_audio_output_mode"]
    enabled = namespace["_timeline_video_audio_enabled"]

    timeline = {"audioOutputMode": "source", "videoAudioEnabled": False}
    assert normalize(timeline) == "source"
    assert enabled(timeline) is True
    assert enabled({"audioOutputMode": "generate", "videoAudioEnabled": True}) is False
    assert enabled({"audioOutputMode": "silent"}) is False
    assert enabled({"videoAudioEnabled": False}) is False
    assert enabled({}) is True
    with pytest.raises(ValueError, match="audioOutputMode"):
        normalize({"audioOutputMode": "legacy"})


@pytest.mark.parametrize(
    "requested,legacy_toggle,mode,audible,standalone,expected,lock_kind,silent",
    [
        ("legacy", False, None, True, False, "generate", None, False),
        ("legacy", True, None, True, False, "source", "timeline_video_audio", False),
        ("legacy", None, None, True, False, "source", "timeline_video_audio", False),
        ("legacy", False, "source", True, False, "source", "timeline_video_audio", False),
        ("source", False, None, False, False, "source", None, False),
        ("generate", True, None, True, False, "generate", None, False),
        ("generate", True, None, True, True, "generate", None, False),
        ("silent", True, None, True, True, "silent", None, True),
        ("legacy", True, "silent", True, False, "silent", None, True),
    ],
)
def test_audio_policy_resolves_legacy_and_explicit_modes(
    requested, legacy_toggle, mode, audible, standalone, expected, lock_kind, silent,
):
    plan = _finite_plan(
        legacy_toggle, output_mode=mode, has_video_audio=audible, standalone=standalone,
    )
    finite, namespace = _policy_functions(plan)

    resolved, locked_audio, force_silence = namespace["_finite_audio_output_policy"](
        finite, requested,
    )

    assert resolved == expected
    assert (locked_audio or {}).get("lockKind") == lock_kind
    assert force_silence is silent


class _MockGraphNode:
    def __init__(self, node_type, node_id, inputs):
        self.node_type = node_type
        self.node_id = node_id
        self.inputs = inputs

    def out(self, index):
        return (self.node_id, index)


class _MockGraph:
    def __init__(self):
        self.nodes = []

    def node(self, node_type, id=None, **inputs):
        node = _MockGraphNode(node_type, id, inputs)
        self.nodes.append(node)
        return node


@pytest.mark.parametrize("stage", ["full", "preview", "finalize"])
@pytest.mark.parametrize(
    "mode,audible,standalone,expected_nodes",
    [
        ("source", True, False, ["MiniMaxH3LockedAudioSlice", "VAEEncodeAudio", "MiniMaxH3LockAudioLatent", "MiniMaxH3LockedAudioMaster"]),
        ("source", False, False, []),
        ("generate", True, False, []),
        ("generate", True, True, ["MiniMaxH3LockedAudioSlice", "VAEEncodeAudio", "MiniMaxH3LockAudioLatent", "MiniMaxH3LockedAudioMaster"]),
        ("silent", True, True, ["MiniMaxH3SilentAudioSlice", "VAEEncodeAudio", "MiniMaxH3LockAudioLatent", "MiniMaxH3SilentAudioMaster"]),
    ],
)
def test_full_preview_and_finalize_graphs_share_audio_policy(
    stage, mode, audible, standalone, expected_nodes,
):
    plan = _finite_plan(True, has_video_audio=audible, standalone=standalone)
    finite, namespace = _policy_functions(plan)
    resolved, locked_audio, silent = namespace["_finite_audio_output_policy"](finite, mode)
    graph = _MockGraph()
    segment_plan = {"timeline": dict(plan["timeline"])}

    latent = namespace["_finite_audio_latent_graph"](
        graph, segment_plan=segment_plan, segment_number=1, audio_vae="audio-vae",
        sampling_latent="sample-target", locked_audio=locked_audio, silent_audio=silent,
    )
    graph.node("MiniMaxH3TimelineSelfLiftSampler", id="sample_1", latent_image=latent, execution_stage=stage)
    master = namespace["_finite_audio_master_graph"](
        graph, finite_plan=finite, merged_audio="sampled-audio",
        locked_audio=locked_audio, silent_audio=silent,
    )

    assert resolved == mode
    if not expected_nodes:
        assert master == "sampled-audio"
    assert [node.node_type for node in graph.nodes if node.node_type != "MiniMaxH3TimelineSelfLiftSampler"] == expected_nodes
    assert master == ("sampled-audio" if not expected_nodes else (graph.nodes[-1].node_id, 0))
    if len(graph.nodes) >= 2:
        assert graph.nodes[-2].inputs.get("execution_stage") == stage


def test_sampler_advertises_keyword_and_propagates_mode_into_every_segment_plan():
    section = _section(SAMPLER_FILE)
    assert 'io.Combo.Input("audio_output_mode", options=["legacy", "source", "generate", "silent"], default="legacy", optional=True)' in section
    assert 'audio_output_mode="legacy"' in section
    assert 'resolved_audio_mode = _resolve_audio_output_mode(finite, audio_output_mode)' in section
    assert 'segment_plan["timeline"]["audioOutputMode"] = resolved_audio_mode' in section
    assert "execution_stage=stages[index]" in section
    assert "_finite_audio_latent_graph(" in section
    assert "_finite_audio_master_graph(" in section


def test_current_managed_patch_fingerprint_is_allowlisted_for_exact_upgrade():
    spec = importlib.util.spec_from_file_location("director_patch_tool_audio_hash", PATCH_TOOL)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert "6f3f522a0a68bf2fa04e3ccbeb425d623cc80318092ec5388fffb72933b2fa37" in tool.PREVIOUS_FULL_PATCH_SHA256


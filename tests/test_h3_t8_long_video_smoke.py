from pathlib import Path
import copy
import importlib.util
import json


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "h3_t8_long_video_smoke.py"


def load_module():
    spec = importlib.util.spec_from_file_location("h3_t8_long_video_smoke_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_prompt_is_exact_two_segment_stock20_relay_eav():
    module = load_module()
    graph = module.build_prompt("release_smoke_test", 17)
    relay = graph["5"]["inputs"]
    runner = graph["6"]["inputs"]

    assert graph["1"]["inputs"]["unet_name"] == module.UNET
    assert graph["2"]["inputs"]["clip_name"] == module.CLIP
    assert relay["length"] == 192
    assert relay["timing_mode"] == "frames"
    assert relay["time_ranges"] == "0-123\n124-183\n184-191"
    assert runner["chain_id"] == "release_smoke_test"
    assert runner["total_duration_seconds"] == 8.0
    assert [runner["width"], runner["height"]] == [512, 288]
    assert runner["render_window_frames"] == 124
    assert runner["context_frames"] == 22
    assert runner["steps"] == 20
    assert runner["prompt_relay_mode"] == "apply_exp"
    assert runner["eav_mode"] == "apply_exp"
    assert runner["task_type"] == "auto"
    assert runner["add_source_as_reference"] is True
    assert runner["resume_existing"] is True
    assert runner["prompt_relay_plan"] == ["5", 0]


def test_validate_interrupted_and_complete_state_contracts():
    module = load_module()
    base = {
        "schema": 1,
        "format": "minimax_h3_t8_in_node_loop_effects",
        "chain_id": "chain",
        "segment_count": 2,
        "contract_sha256": "a" * 64,
    }
    interrupted = {
        **base,
        "status": "interrupted",
        "accepted_count": 1,
        "manifest_revision": 1,
        "current_segment_index": 1,
    }
    module.validate_state(
        interrupted, chain_id="chain", status="interrupted", accepted_count=1
    )

    complete = {
        **base,
        "status": "complete",
        "accepted_count": 2,
        "manifest_revision": 2,
        "current_segment_index": None,
    }
    module.validate_state(
        complete, chain_id="chain", status="complete", accepted_count=2
    )


def test_validate_manifest_requires_124_plus_68_frames():
    module = load_module()
    manifest = {
        "schema": 2,
        "format": "minimax_h3_t8_accepted_manifest",
        "chain_id": "chain",
        "revision": 2,
        "segments": [
            {
                "index": 0,
                "fps": 24,
                "width": 512,
                "height": 288,
                "frame_count": 124,
                "is_final_segment": False,
            },
            {
                "index": 1,
                "fps": 24,
                "width": 512,
                "height": 288,
                "frame_count": 68,
                "is_final_segment": True,
            },
        ],
    }
    module.validate_manifest(manifest, "chain", 2)

    broken = copy.deepcopy(manifest)
    broken["segments"][1]["frame_count"] = 69
    try:
        module.validate_manifest(broken, "chain", 2)
    except RuntimeError as exc:
        assert "68" in str(exc)
    else:
        raise AssertionError("69-frame final segment must be rejected")


def test_validate_final_report_requires_relay_and_eav_on_both_segments(tmp_path):
    module = load_module()
    report = {
        "status": "complete",
        "accepted_count": 2,
        "resume_action": "generated_or_resumed_then_completed",
        "effect_contract": {
            "prompt_relay_mode": "apply_exp",
            "eav_mode": "apply_exp",
        },
        "segment_audits": [
            {
                "prompt_relay": {"status": "applied_exp"},
                "enhance_a_video_audit": {"status": "verified"},
            },
            {
                "prompt_relay": {"status": "applied_exp"},
                "enhance_a_video_audit": {"status": "verified"},
            },
        ],
    }
    (tmp_path / module.REPORT_NAME).write_text(json.dumps(report), encoding="utf-8")
    assert module.validate_final_report(tmp_path) == report


def test_discover_output_root_accepts_explicit_path(tmp_path):
    module = load_module()
    output = tmp_path / "custom-output"
    assert module.discover_output_root(tmp_path, str(output)) == output.resolve()
    assert output.is_dir()

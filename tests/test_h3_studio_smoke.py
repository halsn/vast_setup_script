from pathlib import Path
import runpy
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "h3_studio_smoke.py"


def test_h3_studio_smoke_help_is_network_free():
    result = subprocess.run(
        [sys.executable, str(SMOKE), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--generate" in result.stdout
    assert "real T2V generation" in result.stdout


def test_h3_studio_smoke_defaults_do_not_generate():
    module = runpy.run_path(str(SMOKE), run_name="h3_studio_smoke_test")
    args = module["parse_args"]([])
    assert args.generate is False
    assert args.timeline_model == "fused"
    assert args.studio_url == "http://127.0.0.1:18080"
    assert args.comfy_url == "http://127.0.0.1:18188"
    assert args.width == 480
    assert args.height == 288
    assert args.duration == 2
    assert args.steps == 4


def test_h3_studio_smoke_rejects_non_h3_canvas_width():
    result = subprocess.run(
        [sys.executable, str(SMOKE), "--width", "481"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "multiple of 32" in result.stderr


def test_h3_studio_smoke_contract_matches_deployed_timeline_alias():
    text = SMOKE.read_text(encoding="utf-8")
    assert 'TIMELINE_SOURCE = "ComfyUI-MiniMaxH3-TimelineDirector"' in text
    assert 'TIMELINE_TEMPLATE = "h3_timeline_director"' in text
    assert 'TIMELINE_SOURCE_UNET = "minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors"' in text
    assert 'TIMELINE_CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"' in text
    assert '"fused": {' in text
    assert '"native": {' in text
    assert '"steps": 8' in text
    assert '"steps": 20' in text
    assert 'T8_DIRECTOR_NODE = "MiniMaxH3DirectorProjectT8"' in text
    assert 'T8_DIRECTOR_UI = "/minimax_h3_t8/director/ui"' in text
    assert 'T8_DIRECTOR_CAPABILITIES = "/minimax_h3_t8/director/capabilities"' in text
    assert 'T8_DIRECTOR_SCHEMA = "t8.minimax_h3.director_capabilities.v1"' in text
    assert '"MiniMaxH3TimelinePlanner"' in text
    assert '"MiniMaxH3FiniteSegmentSampler"' in text
    assert '"MiniMaxH3TimelineSelfLiftSampler"' in text
    assert "/api/comfyui/status" in text
    assert "/workflow_templates" in text

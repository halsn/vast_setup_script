from pathlib import Path


SCRIPT = Path("scripts/setupp_h3_comfui_turbo.sh")


def test_turbo_profile_requires_comfyui_031_and_installs_matching_workflow():
    content = SCRIPT.read_text(encoding="utf-8")

    assert '"0.31.0"' in content
    assert "video_minimax_h3_t2v_lightx2v_turbo.json" in content
    assert "H3_Fast_Turbo.json" in content
    assert "minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors" in content

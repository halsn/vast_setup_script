import json
from pathlib import Path


SCRIPT = Path("scripts/setupp_h3_comfui_turbo.sh")
CATALOG = Path("config/templates.json")


def test_turbo_profile_requires_comfyui_031_and_installs_matching_lora():
    content = SCRIPT.read_text(encoding="utf-8")

    assert '"0.31.0"' in content
    assert "minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors" in content


def test_turbo_template_reuses_the_base_t2v_readiness_workflow():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    turbo = next(item for item in catalog["templates"] if item["id"] == "h3_fast_turbo")

    assert turbo["workflow"] == "H3_T2V_Accelerated.json"
    assert turbo["features"] == ["h3", "turbo"]

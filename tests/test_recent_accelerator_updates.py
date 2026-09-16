from pathlib import Path
import json


CACHE = Path("scripts/setupp_h3_comfui_cache.sh")
FASTH3 = Path("scripts/setupp_h3_comfui_fasth3.sh")
REGISTRY = Path("config/deployment_profiles.json")


def test_cache_pins_recent_spectrum_and_firstblock_revisions():
    text = CACHE.read_text(encoding="utf-8")

    assert 'H3_CACHE_SPECTRUM_REV="${H3_CACHE_SPECTRUM_REV:-120d72e2f48b781235b34149e39bbdf0f1317d82}"' in text
    assert 'H3_CACHE_FIRSTBLOCK_REV="${H3_CACHE_FIRSTBLOCK_REV:-f7a27128e73e1859f2295e64698164203799029d}"' in text
    assert "install_cache_node_pinned" in text


def test_firstblock_cache_exposes_current_presets_in_generated_workflow():
    text = CACHE.read_text(encoding="utf-8")

    assert 'H3_CACHE_PRESET="${H3_CACHE_PRESET:-fast}"' in text
    for preset in ("safe", "fast", "aggressive", "experimental"):
        assert preset in text
    assert '"H3 Experimental"' in text
    assert '"H3 Fast — 0.10 / max 2"' in text


def test_fasth3_defaults_to_official_8step_v2_comfy_assets_and_templates():
    text = FASTH3.read_text(encoding="utf-8")

    assert 'H3_FASTH3_VARIANT="${H3_FASTH3_VARIANT:-v2_8step}"' in text
    assert 'FastVideo/FastVideo-FastH3-Comfy' in text
    assert 'fastvideo_fasth3_8step_v2_pruned_int8_convrot.safetensors' in text
    assert 'video_fastvideo_fasth3_t2v.json' in text
    assert 'video_fastvideo_fasth3_i2v.json' in text
    assert 'Comfy-Org/workflow_templates' in text


def test_fasth3_keeps_legacy_4step_preview_as_explicit_compatibility_mode():
    text = FASTH3.read_text(encoding="utf-8")

    assert "preview4" in text
    assert 'barelymining/ComfyUI-MiniMax-H3-FastVideo.git' in text
    assert 'fasth3_vsa_4-steps-v5.safetensors' in text
    assert 'fasth3_vsa_gate.safetensors' in text


def test_registry_describes_current_cache_and_fasth3_defaults():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    profiles = {profile["id"]: profile for profile in registry["profiles"]}

    assert "Spectrum v0.2.27" in profiles["cache"]["notes"]
    assert "FastH3 8-Step V2" in profiles["fasth3"]["notes"]

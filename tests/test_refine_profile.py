from pathlib import Path


SCRIPT = Path("scripts/setupp_h3_comfui_refine.sh")


def test_refine_profile_is_discoverable_and_pins_runtime_assets():
    assert SCRIPT.name.startswith("setupp_h3_comfui")
    assert SCRIPT.exists(), "H3 refine deployment profile script is missing"

    text = SCRIPT.read_text(encoding="utf-8")
    assert "h3_profile_prepare_base" in text
    assert "xmarre/Comfyui_Minimax_h3_latent_Upscaler-Plus.git" in text
    assert "620165a311de9b28a36260219fb5cd370a304e3c" in text
    assert "minimax_h3_latent_upscaler_3d_fp16.safetensors" in text
    assert "043e5a48e161610ef6c3ea974645220354d06fa618abca15f76d084812eb55c2" in text
    assert "h3_profile_finish" in text


def test_refine_profile_preflights_disk_and_verifies_checksum():
    assert SCRIPT.exists(), "H3 refine deployment profile script is missing"

    text = SCRIPT.read_text(encoding="utf-8")
    assert 'REFINE_REQUIRED_FREE_GB="${REFINE_REQUIRED_FREE_GB:-3}"' in text
    assert "sha256" in text.lower()
    assert "latent_upscale_models" in text

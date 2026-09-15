from pathlib import Path
import json


BASE_SCRIPT = Path("scripts/setupp_h3_comfui.sh")
REFINE_SCRIPT = Path("scripts/setupp_h3_comfui_refine.sh")
REGISTRY = Path("config/deployment_profiles.json")
PROFILE_SCRIPTS = [
    Path("scripts/setupp_h3_comfui_cache.sh"),
    Path("scripts/setupp_h3_comfui_fasth3.sh"),
    Path("scripts/setupp_h3_comfui_pdd.sh"),
    Path("scripts/setupp_h3_comfui_turbo.sh"),
    Path("scripts/setupp_h3_comfui_vdn.sh"),
]


def test_base_h3_installs_refine_and_packed_latent_persistence_by_default():
    text = BASE_SCRIPT.read_text(encoding="utf-8")

    assert 'H3_INSTALL_REFINE="${H3_INSTALL_REFINE:-1}"' in text
    assert "https://github.com/xmarre/Comfyui_Minimax_h3_latent_Upscaler-Plus.git" in text
    assert "620165a311de9b28a36260219fb5cd370a304e3c" in text
    assert "minimax_h3_latent_upscaler_3d_fp16.safetensors" in text
    assert "043e5a48e161610ef6c3ea974645220354d06fa618abca15f76d084812eb55c2" in text
    assert "https://github.com/MadPonyInteractive/ComfyUi-MpiNodes.git" in text
    assert "1de35a33827b125fe2adbc08df23266c465c032a" in text
    assert '"MpiSaveLatent"' in text
    assert '"MpiLoadLatent"' in text

    main = text[text.index("main() {") : text.index('if [[ "${H3_BOOTSTRAP_LIB_ONLY:-0}"')]
    assert "install_refine_capability" in main
    assert "run_health_checks" in main


def test_refine_can_only_be_disabled_explicitly():
    text = BASE_SCRIPT.read_text(encoding="utf-8")

    assert 'if [[ "${H3_INSTALL_REFINE:-1}" != "1" ]]' in text
    assert "Refine capability disabled by H3_INSTALL_REFINE=0" in text


def test_every_acceleration_profile_inherits_base_refine_capability():
    for script in PROFILE_SCRIPTS:
        text = script.read_text(encoding="utf-8")
        assert "h3_profile_prepare_base" in text, f"{script.name} must execute the H3 base bootstrap"


def test_refine_is_not_a_separate_deployment_profile():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))

    assert not REFINE_SCRIPT.exists()
    assert all(profile["id"] != "refine" for profile in registry["profiles"])

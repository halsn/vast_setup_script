from pathlib import Path
import json


SCRIPT = Path("scripts/setupp_h3_comfui_refine.sh")
REGISTRY = Path("config/deployment_profiles.json")


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


def test_refine_profile_pins_and_verifies_mpi_latent_persistence_nodes():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "https://github.com/MadPonyInteractive/ComfyUi-MpiNodes.git" in text
    assert "1de35a33827b125fe2adbc08df23266c465c032a" in text
    assert "install_pinned_mpi_nodes" in text
    assert "verify_mpi_latent_nodes" in text
    assert '"MpiSaveLatent"' in text
    assert '"MpiLoadLatent"' in text

    main = text[text.index("main_refine() {") : text.index('main_refine "$@"')]
    assert main.index("install_pinned_mpi_nodes") < main.index("h3_profile_finish")
    assert main.index("h3_profile_finish") < main.index("verify_mpi_latent_nodes")


def test_refine_profile_is_registered_for_discovery_and_documents_latent_persistence():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    refine = next((profile for profile in registry["profiles"] if profile["id"] == "refine"), None)

    assert refine is not None
    assert refine["script"] == "scripts/setupp_h3_comfui_refine.sh"
    assert refine["status"] == "stable"
    assert "latent" in refine["notes"].lower()

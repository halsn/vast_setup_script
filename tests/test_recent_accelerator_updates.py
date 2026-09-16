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


def test_fasth3_model_download_is_pinned_to_the_verified_hugging_face_revision():
    text = FASTH3.read_text(encoding="utf-8")

    assert 'H3_FASTH3_V2_REV="${H3_FASTH3_V2_REV:-567165f0412203f6629b98982f82a154bd7474a0}"' in text
    assert "install_fasth3_v2_model" in text
    assert "revision=revision" in text
    assert '"$H3_FASTH3_V2_REV"' in text


def test_fasth3_model_file_is_sha256_verified_before_reuse_and_after_download():
    text = FASTH3.read_text(encoding="utf-8")

    assert 'H3_FASTH3_V2_SHA256="${H3_FASTH3_V2_SHA256:-0922785978dc9bfe1adf27d8b291b0ca763f9f165f882e6cb297c72fbb6deda8}"' in text
    assert "hashlib.sha256" in text
    assert "FastH3 V2 checkpoint SHA-256 mismatch" in text
    assert "os.remove(dst)" in text
    assert "Downloaded FastH3 V2 checkpoint failed SHA-256 verification" in text


def test_fasth3_reference_workflows_are_pinned_to_the_verified_upstream_revision():
    text = FASTH3.read_text(encoding="utf-8")

    assert "90c71fb78b3726392d010ff62a8e79e92d7296ad" in text
    assert "workflow_templates/main/templates" not in text


def test_fasth3_v2_requires_current_comfy_core_and_sparse_attention_nodes():
    text = FASTH3.read_text(encoding="utf-8")

    assert 'H3_FASTH3_V2_MIN_COMFYUI_VERSION="${H3_FASTH3_V2_MIN_COMFYUI_VERSION:-0.35.0}"' in text
    assert "MiniMaxH3SigmaShift" in text
    assert "ModelAttentionBackend" in text
    assert "BlockSparseAttention" in text
    assert "verify_fasth3_v2_readiness" in text


def test_fasth3_v2_upgrades_old_comfyui_to_a_pinned_verified_core():
    text = FASTH3.read_text(encoding="utf-8")

    assert 'H3_FASTH3_V2_COMFYUI_REF="${H3_FASTH3_V2_COMFYUI_REF:-v0.35.0}"' in text
    assert 'git -C "$COMFY_DIR" checkout --detach "$H3_FASTH3_V2_COMFYUI_REF"' in text
    assert 'update_git_checkout "$COMFY_DIR" "ComfyUI"' not in text


def test_fasth3_workbench_scope_is_explicitly_t2va_until_fl2va_gpu_validation():
    text = FASTH3.read_text(encoding="utf-8")

    assert "T2VA" in text
    assert "FL2VA" in text
    assert "GPU validation" in text


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

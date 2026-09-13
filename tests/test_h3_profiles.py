from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCRIPTS = [
    "scripts/setupp_h3_comfui_turbo.sh",
    "scripts/setupp_h3_comfui_cache.sh",
    "scripts/setupp_h3_comfui_pdd.sh",
    "scripts/setupp_h3_comfui_vdn.sh",
    "scripts/setupp_h3_comfui_fasth3.sh",
]
SCRIPTS = ["scripts/h3_profile_common.sh", *PROFILE_SCRIPTS]


def test_h3_profile_scripts_are_flat_and_parse():
    for rel in SCRIPTS:
        path = ROOT / rel
        assert path.is_file(), rel
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_no_experimental_script_directory():
    assert not (ROOT / "scripts" / "experimental").exists()


def test_legacy_fast_script_is_removed():
    assert not (ROOT / "scripts" / "setupp_h3_comfui_fast.sh").exists()


def test_deployment_profile_registry_points_to_real_scripts():
    registry = json.loads((ROOT / "config" / "deployment_profiles.json").read_text())
    ids = set()
    for profile in registry["profiles"]:
        assert profile["id"] not in ids
        ids.add(profile["id"])
        assert profile["id"] != "legacy-fast"
        assert (ROOT / profile["script"]).is_file(), profile["script"]


def test_profile_scripts_can_load_common_helper_before_branch_merge():
    for rel in PROFILE_SCRIPTS:
        text = (ROOT / rel).read_text()
        assert "H3_PROFILE_COMMON_URL" in text, rel
        assert "vast_setup_script/main/scripts/h3_profile_common.sh" in text, rel
        assert "vast_setup_script/refactor-h3-deploy-scripts/scripts/h3_profile_common.sh" in text, rel


def test_cache_profile_owns_cache_implementation():
    text = (ROOT / "scripts" / "setupp_h3_comfui_cache.sh").read_text()
    assert "setupp_h3_comfui_fast.sh" not in text
    assert "h3_profile_common.sh" in text
    assert "ComfyUI-Spectrum-MiniMax-H3" in text
    assert "ComfyUI-MiniMaxH3-FirstBlockCache" in text

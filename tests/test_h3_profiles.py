from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [
    "scripts/h3_profile_common.sh",
    "scripts/setupp_h3_comfui_fast.sh",
    "scripts/setupp_h3_comfui_turbo.sh",
    "scripts/setupp_h3_comfui_cache.sh",
    "scripts/setupp_h3_comfui_pdd.sh",
    "scripts/setupp_h3_comfui_vdn.sh",
    "scripts/setupp_h3_comfui_fasth3.sh",
]


def test_h3_profile_scripts_are_flat_and_parse():
    for rel in SCRIPTS:
        path = ROOT / rel
        assert path.is_file(), rel
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_no_experimental_script_directory():
    assert not (ROOT / "scripts" / "experimental").exists()


def test_deployment_profile_registry_points_to_real_scripts():
    registry = json.loads((ROOT / "config" / "deployment_profiles.json").read_text())
    ids = set()
    for profile in registry["profiles"]:
        assert profile["id"] not in ids
        ids.add(profile["id"])
        assert (ROOT / profile["script"]).is_file(), profile["script"]


def test_legacy_fast_is_only_a_compatibility_dispatcher():
    path = ROOT / "scripts" / "setupp_h3_comfui_fast.sh"
    text = path.read_text()
    assert len(text.splitlines()) <= 120
    assert "setupp_h3_comfui_cache.sh" in text
    assert "setupp_h3_comfui_turbo.sh" in text
    assert "ComfyUI-Spectrum-MiniMax-H3" not in text
    assert "ComfyUI-MiniMaxH3-FirstBlockCache" not in text
    assert "Larryvrh/ComfyUI-MiniMax-H3-Turbo" not in text


def test_cache_profile_owns_cache_implementation():
    text = (ROOT / "scripts" / "setupp_h3_comfui_cache.sh").read_text()
    assert "setupp_h3_comfui_fast.sh" not in text
    assert "h3_profile_common.sh" in text
    assert "ComfyUI-Spectrum-MiniMax-H3" in text
    assert "ComfyUI-MiniMaxH3-FirstBlockCache" in text

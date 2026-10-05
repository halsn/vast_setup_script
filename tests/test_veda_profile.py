import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "h3_veda_assets.py"
STUDIO = ROOT / "scripts" / "setupp_h3_studio.sh"


def load_installer():
    spec = spec_from_file_location("h3_veda_assets", INSTALLER)
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_studio_profile_installs_pinned_veda_node_and_verified_assets_before_restart():
    text = STUDIO.read_text(encoding="utf-8")

    assert 'VEDA_NODE_NAME="Veda-on-ComfyUI"' in text
    assert 'VEDA_NODE_REV="fd59c7277ccc37ebf1a8f6823474b8c2ef2e33a0"' in text
    assert 'h3_studio_install_veda' in text
    assert 'h3_veda_assets.py' in text
    assert (
        'H3_VEDA_ASSETS_TOOL_URL="${H3_VEDA_ASSETS_TOOL_URL:-'
        'https://raw.githubusercontent.com/halsn/vast_setup_script/'
        '$H3_SETUP_SUPPORT_REV/scripts/h3_veda_assets.py}"'
    ) in text
    main = text.split("main_studio() {", 1)[1]
    assert main.index("  h3_studio_install_veda\n") < main.index("  h3_profile_finish\n")
    assert "git -C \"$target\" status --porcelain" in text


def test_veda_models_are_pinned_to_published_revisions_and_lfs_hashes():
    installer = load_installer()
    assets = {item.filename: item for item in installer.VEDA_ASSETS}

    predictor = assets["minimax_h3_t2va_veda_8nfe_600step_preview_fp8.safetensors"]
    assert predictor.repo == "Veda-Sparse/Minimax-H3-T2VA-Veda-8NFE-600Step-Preview"
    assert predictor.revision == "9a1fd3a41b4a754a7886e64e82edbddf599fd1bd"
    assert predictor.size_bytes == 275415648
    assert predictor.sha256 == "2a8d8845c5342756a2781e8e69563940e4bb573c9a40ebb534915ff8fd76573a"

    lora = assets["minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors"]
    assert lora.repo == "lightx2v/Minimax-h3-Turbo"
    assert lora.revision == "3ec17a324ced54151364f24f8b5fb6bf7e26414f"
    assert lora.size_bytes == 1956193000
    assert lora.sha256 == "08cfe946033af7d27719b964b6e0a0e50c32138daabbd6ce4137e23df6bf9980"


def test_asset_install_is_atomic_pinned_and_reuses_a_verified_file(tmp_path):
    installer = load_installer()
    content = b"verified mock model bytes"
    asset = installer.PinnedAsset(
        repo="test/model",
        revision="0123456789abcdef0123456789abcdef01234567",
        filename="predictor.safetensors",
        target="veda/predictor.safetensors",
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    calls = []

    def mock_download(repo, revision, filename, local_dir):
        calls.append((repo, revision, filename))
        path = Path(local_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    destination = installer.install_pinned_asset(asset, tmp_path, mock_download)
    assert destination.read_bytes() == content
    assert calls == [(asset.repo, asset.revision, asset.filename)]
    assert list((tmp_path / "veda").glob(".h3-veda-download-*")) == []

    installer.install_pinned_asset(asset, tmp_path, lambda *args: pytest.fail("verified file re-downloaded"))


def test_asset_install_rejects_bad_download_and_preserves_existing_file(tmp_path):
    installer = load_installer()
    expected = b"expected"
    asset = installer.PinnedAsset(
        repo="test/model",
        revision="0123456789abcdef0123456789abcdef01234567",
        filename="predictor.safetensors",
        target="veda/predictor.safetensors",
        size_bytes=len(expected),
        sha256=hashlib.sha256(expected).hexdigest(),
    )
    destination = tmp_path / asset.target
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"user file")

    with pytest.raises(RuntimeError, match="does not match the pinned release"):
        installer.install_pinned_asset(asset, tmp_path, lambda *args: None)
    assert destination.read_bytes() == b"user file"

    destination.unlink()

    def corrupt_download(repo, revision, filename, local_dir):
        path = Path(local_dir) / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"notmatch")
        return path

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        installer.install_pinned_asset(asset, tmp_path, corrupt_download)
    assert not destination.exists()
    assert list(destination.parent.glob(".h3-veda-download-*")) == []

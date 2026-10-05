#!/usr/bin/env python3
"""Install the pinned ComfyUI Veda predictor and its 8-step H3 LoRA."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Callable


@dataclass(frozen=True)
class PinnedAsset:
    repo: str
    revision: str
    filename: str
    target: str
    size_bytes: int
    sha256: str


VEDA_ASSETS = (
    PinnedAsset(
        repo="Veda-Sparse/Minimax-H3-T2VA-Veda-8NFE-600Step-Preview",
        revision="9a1fd3a41b4a754a7886e64e82edbddf599fd1bd",
        filename="minimax_h3_t2va_veda_8nfe_600step_preview_fp8.safetensors",
        target="veda/minimax_h3_t2va_veda_8nfe_600step_preview_fp8.safetensors",
        size_bytes=275415648,
        sha256="2a8d8845c5342756a2781e8e69563940e4bb573c9a40ebb534915ff8fd76573a",
    ),
    PinnedAsset(
        repo="lightx2v/Minimax-h3-Turbo",
        revision="3ec17a324ced54151364f24f8b5fb6bf7e26414f",
        filename="minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",
        target="loras/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors",
        size_bytes=1956193000,
        sha256="08cfe946033af7d27719b964b6e0a0e50c32138daabbd6ce4137e23df6bf9980",
    ),
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def matches_release(path: Path, asset: PinnedAsset) -> bool:
    return (
        path.is_file()
        and path.stat().st_size == asset.size_bytes
        and sha256_file(path) == asset.sha256
    )


def _huggingface_download(repo: str, revision: str, filename: str, local_dir: str) -> Path:
    from huggingface_hub import hf_hub_download

    return Path(
        hf_hub_download(
            repo_id=repo,
            revision=revision,
            filename=filename,
            local_dir=local_dir,
        )
    )


def install_pinned_asset(
    asset: PinnedAsset,
    models_root: Path,
    download_file: Callable[[str, str, str, str], Path] | None = None,
) -> Path:
    models_root = Path(models_root)
    destination = models_root / asset.target
    if destination.exists():
        if matches_release(destination, asset):
            print(f"[OK] verified pinned Veda asset: {destination}")
            return destination
        raise RuntimeError(
            f"Existing Veda asset does not match the pinned release; refusing to overwrite: {destination}"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    download_file = download_file or _huggingface_download
    free_bytes = shutil_disk_usage(destination.parent)
    if free_bytes < asset.size_bytes + 512 * 1024 * 1024:
        raise RuntimeError(
            f"Installing {asset.filename} needs {asset.size_bytes / (1024**3):.2f} GiB "
            "plus a 512 MiB reserve on the ComfyUI models filesystem"
        )
    with tempfile.TemporaryDirectory(
        prefix=".h3-veda-download-", dir=destination.parent
    ) as staging_dir:
        downloaded = Path(
            download_file(asset.repo, asset.revision, asset.filename, staging_dir)
        )
        if not downloaded.is_file():
            raise RuntimeError(f"Veda asset download did not produce a file: {asset.filename}")
        actual_size = downloaded.stat().st_size
        if actual_size != asset.size_bytes:
            raise RuntimeError(
                f"Veda asset size mismatch for {asset.filename}: "
                f"expected {asset.size_bytes}, got {actual_size}"
            )
        actual_hash = sha256_file(downloaded)
        if actual_hash != asset.sha256:
            raise RuntimeError(
                f"Veda asset SHA-256 mismatch for {asset.filename}: "
                f"expected {asset.sha256}, got {actual_hash}"
            )
        os.replace(downloaded, destination)

    print(f"[OK] installed verified Veda asset: {destination}")
    return destination


def shutil_disk_usage(path: Path) -> int:
    import shutil

    return shutil.disk_usage(path).free


def install_veda_assets(models_root: Path) -> list[Path]:
    return [install_pinned_asset(asset, models_root) for asset in VEDA_ASSETS]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("models_root", type=Path)
    args = parser.parse_args()
    install_veda_assets(args.models_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

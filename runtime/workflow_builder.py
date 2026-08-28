"""Build the small, native ComfyUI API graphs used by the desktop client.

The desktop protocol deliberately carries logical template IDs instead of
shipping the UI canvas format.  This module is the single translation point
from that protocol to the API prompt accepted by ``POST /prompt``.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence


VIDEO_MODEL = "minimax_h3_video_vae_fp16.safetensors"
AUDIO_MODEL = "minimax_h3_audio_vae_fp32.safetensors"
TEXT_MODEL = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
FL2VA_MODEL = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
REF2VA_MODEL = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
TURBO_LORA = "minimax_h3_turbo_v4_step600_ema.safetensors"

_TEMPLATE_MODES = {
    "h3_t2v": "t2v",
    "h3_fast_turbo": "t2v",
    "h3_i2v": "i2v",
    "h3_r2v": "r2v",
}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


class WorkflowBuildError(ValueError):
    """Raised when a desktop submission cannot become a valid H3 graph."""


def _node(class_type: str, **inputs: Any) -> dict[str, Any]:
    return {"class_type": class_type, "inputs": inputs}


def _connection(node_id: str, output: int = 0) -> list[Any]:
    return [node_id, output]


def _remote_filename(remote_id: str) -> str:
    value = str(remote_id).replace("\\", "/").strip()
    if not value:
        raise WorkflowBuildError("asset remote_id must not be empty")
    parts = [part for part in PurePosixPath(value).parts if part not in {"", "."}]
    if parts and parts[0] in {"input", "output", "temp"}:
        parts = parts[1:]
    if not parts or any(part == ".." for part in parts):
        raise WorkflowBuildError("asset remote_id is invalid")
    return "/".join(parts)


def _asset_role(asset: Mapping[str, Any]) -> str:
    role = asset.get("role")
    if not isinstance(role, str) or not role.strip():
        raise WorkflowBuildError("asset role must be a non-empty string")
    return role.strip()


def _asset_path(asset: Mapping[str, Any]) -> str:
    remote_id = asset.get("remote_id")
    if not isinstance(remote_id, str):
        raise WorkflowBuildError("asset remote_id must be a string")
    return _remote_filename(remote_id)


def _parameters(parameters: Mapping[str, Any]) -> tuple[int, int, int, int]:
    values = []
    for name in ("width", "height", "frames", "seed"):
        value = parameters.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise WorkflowBuildError(f"parameter {name} must be an integer")
        values.append(value)
    width, height, frames, seed = values
    if width < 32 or height < 32 or frames < 5:
        raise WorkflowBuildError("H3 requires width/height >= 32 and at least 5 frames")
    return width, height, _align_frames(frames), seed


def _align_frames(frames: int) -> int:
    """Snap the requested frame count to H3's 17k+5 temporal grid."""

    frames = max(5, frames)
    return frames + ((5 - frames) % 17)


def _prompt(prompt: str, negative_prompt: str) -> str:
    value = prompt.strip()
    if not value:
        raise WorkflowBuildError("prompt must not be empty")
    negative = negative_prompt.strip()
    return f"{value}\n\nAvoid: {negative}" if negative else value


def _base_graph(
    *,
    model_name: str,
    prompt: str,
    width: int,
    height: int,
    frames: int,
    seed: int,
    steps: int,
    gpu_memory_mib: int | None,
) -> dict[str, dict[str, Any]]:
    workflow: dict[str, dict[str, Any]] = {
        "video_vae": _node("VAELoader", vae_name=VIDEO_MODEL),
        "audio_vae": _node("VAELoader", vae_name=AUDIO_MODEL),
        "unet": _node("UNETLoader", unet_name=model_name, weight_dtype="default"),
        "clip": _node("CLIPLoader", clip_name=TEXT_MODEL, type="minimax", device="default"),
        "noise": _node("RandomNoise", noise_seed=seed, control_after_generate="fixed"),
        "sampler_select": _node("KSamplerSelect", sampler_name="res_multistep"),
        "scheduler": _node(
            "BasicScheduler",
            model=_connection("model"),
            scheduler="simple",
            steps=steps,
            denoise=1.0,
        ),
        "guider": _node(
            "BasicGuider",
            model=_connection("model"),
            conditioning=_connection("conditioner", 0),
        ),
    }
    if steps == 4:
        workflow["turbo_lora"] = _node(
            "MiniMaxH3TurboLoRA",
            model=_connection("unet"),
            lora_name=TURBO_LORA,
            strength=1.0,
            low_vram=bool(gpu_memory_mib is not None and gpu_memory_mib < 24_000),
        )
        model_id = "turbo_lora"
        workflow["turbo_sampler"] = _node("MiniMaxH3TurboSampler")
    else:
        model_id = "unet"

    workflow["scheduler"]["inputs"]["model"] = _connection(model_id)
    workflow["guider"]["inputs"]["model"] = _connection(model_id)
    workflow["sampler"] = _node(
        "SamplerCustomAdvanced",
        noise=_connection("noise"),
        guider=_connection("guider"),
        sampler=_connection("turbo_sampler" if steps == 4 else "sampler_select"),
        sigmas=_connection("scheduler"),
        latent_image=_connection("conditioner", 1),
    )
    workflow["decode_video"] = _node(
        "VAEDecode", samples=_connection("sampler"), vae=_connection("video_vae")
    )
    workflow["decode_audio"] = _node(
        "VAEDecodeAudio", samples=_connection("sampler"), vae=_connection("audio_vae")
    )
    workflow["create_video"] = _node(
        "CreateVideo",
        images=_connection("decode_video"),
        fps=24.0,
        audio=_connection("decode_audio"),
        bit_depth="auto",
        color_space="sRGB",
    )
    workflow["save_video"] = _node(
        "SaveVideo",
        video=_connection("create_video"),
        filename_prefix="video/H3",
        format={"format": "mp4"},
        codec={"codec": "auto"},
    )
    return workflow


def build_h3_prompt(
    template_id: str,
    prompt: str,
    negative_prompt: str,
    parameters: Mapping[str, Any],
    assets: Sequence[Mapping[str, Any]],
    *,
    gpu_memory_mib: int | None = None,
) -> dict[str, dict[str, Any]]:
    """Return a ComfyUI API-format graph for one H3 job."""

    mode = _TEMPLATE_MODES.get(template_id)
    if mode is None:
        raise WorkflowBuildError(f"unsupported H3 template: {template_id}")
    width, height, frames, seed = _parameters(parameters)
    text = _prompt(prompt, negative_prompt)
    steps = 4 if template_id == "h3_fast_turbo" else 20
    workflow = _base_graph(
        model_name=REF2VA_MODEL if mode == "r2v" else FL2VA_MODEL,
        prompt=text,
        width=width,
        height=height,
        frames=frames,
        seed=seed,
        steps=steps,
        gpu_memory_mib=gpu_memory_mib,
    )

    if mode in {"t2v", "i2v"}:
        conditioner_inputs: dict[str, Any] = {
            "clip": _connection("clip"),
            "vae": _connection("video_vae"),
            "prompt": text,
            "width": width,
            "height": height,
            "length": frames,
        }
        if mode == "i2v":
            if len(assets) != 1 or _asset_role(assets[0]) != "first_frame":
                raise WorkflowBuildError("h3_i2v requires exactly one first_frame image")
            path = _asset_path(assets[0])
            if PurePosixPath(path).suffix.lower() in _VIDEO_SUFFIXES:
                raise WorkflowBuildError("h3_i2v first_frame must be an image")
            workflow["first_frame"] = _node("LoadImage", image=path)
            conditioner_inputs["first_frame"] = _connection("first_frame")
        elif assets:
            raise WorkflowBuildError("h3_t2v does not accept input assets")
        workflow["conditioner"] = _node("MiniMaxH3ImageToVideo", **conditioner_inputs)
        return workflow

    if not assets:
        raise WorkflowBuildError("h3_r2v requires at least one reference asset")
    reference_inputs: dict[str, Any] = {
        "clip": _connection("clip"),
        "vae": _connection("video_vae"),
        "audio_vae": _connection("audio_vae"),
        "prompt": text,
        "width": width,
        "height": height,
        "length": frames,
        "ref_image_size": "match",
    }
    image_index = 0
    video_index = 0
    for asset_index, asset in enumerate(assets):
        role = _asset_role(asset)
        path = _asset_path(asset)
        suffix = PurePosixPath(path).suffix.lower()
        if role not in {"reference", "reference_image", "reference_video"}:
            raise WorkflowBuildError(f"unsupported h3_r2v asset role: {role}")
        if role == "reference_video" or suffix in _VIDEO_SUFFIXES:
            load_id = f"reference_video_{video_index}"
            components_id = f"reference_video_components_{video_index}"
            workflow[load_id] = _node("LoadVideo", file=path)
            workflow[components_id] = _node(
                "GetVideoComponents", video=_connection(load_id)
            )
            reference_inputs[f"ref_videos.ref_video_{video_index}"] = _connection(components_id, 0)
            reference_inputs[f"ref_video_audios.ref_video_audio_{video_index}"] = _connection(components_id, 1)
            video_index += 1
        else:
            load_id = f"reference_{image_index}"
            workflow[load_id] = _node("LoadImage", image=path)
            reference_inputs[f"ref_images.ref_image_{image_index}"] = _connection(load_id)
            image_index += 1
    if image_index == 0 and video_index == 0:
        raise WorkflowBuildError("h3_r2v requires at least one image or video reference")
    workflow["reference_conditioner"] = _node(
        "MiniMaxH3ReferenceToVideo", **reference_inputs
    )
    workflow["guider"]["inputs"]["conditioning"] = _connection("reference_conditioner", 0)
    workflow["sampler"]["inputs"]["latent_image"] = _connection("reference_conditioner", 1)
    return workflow

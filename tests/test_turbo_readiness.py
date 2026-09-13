from pathlib import Path

from runtime.worker_readiness import _enabled_features


TURBO_LORA = "minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors"


def test_turbo_feature_requires_lightx2v_lora_not_a_custom_node(tmp_path: Path):
    comfy = tmp_path / "ComfyUI"
    custom_nodes = comfy / "custom_nodes"
    loras = comfy / "models" / "loras"
    custom_nodes.mkdir(parents=True)
    loras.mkdir(parents=True)

    assert "turbo" not in _enabled_features({}, custom_nodes)

    (loras / TURBO_LORA).write_bytes(b"turbo")

    features = _enabled_features({}, custom_nodes)
    assert "turbo" in features

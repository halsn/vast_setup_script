from runtime.workflow_builder import build_h3_prompt


TURBO_LORA = "minimax_h3_fl2v_turbo_4step_v1.2_768p_comfyui_bf16.safetensors"


def test_lightx2v_turbo_uses_core_lora_sigma_shift_euler_and_four_steps():
    graph = build_h3_prompt(
        "h3_fast_turbo",
        "a fox running in snow",
        "",
        {"width": 768, "height": 512, "frames": 56, "seed": 42},
        [],
    )

    assert graph["turbo_lora"] == {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["unet", 0],
            "lora_name": TURBO_LORA,
            "strength_model": 1.0,
        },
    }
    assert graph["turbo_shift"] == {
        "class_type": "MiniMaxH3SigmaShift",
        "inputs": {
            "model": ["turbo_lora", 0],
            "shift_video": 6.0,
            "shift_audio": 3.0,
        },
    }
    assert graph["sampler_select"]["inputs"]["sampler_name"] == "euler"
    assert graph["scheduler"]["inputs"] == {
        "model": ["turbo_shift", 0],
        "scheduler": "simple",
        "steps": 4,
        "denoise": 1.0,
    }
    assert graph["guider"]["inputs"]["model"] == ["turbo_shift", 0]
    assert graph["sampler"]["inputs"]["sampler"] == ["sampler_select", 0]

    class_types = {node["class_type"] for node in graph.values()}
    assert "MiniMaxH3TurboLoRA" not in class_types
    assert "MiniMaxH3TurboSampler" not in class_types

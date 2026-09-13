from runtime.workflow_builder import build_h3_prompt


def test_pdd_t2v_uses_apply_sigmas_euler_and_no_native_scheduler():
    graph = build_h3_prompt(
        "h3_pdd_t2v",
        "a fox running in snow",
        "",
        {"width": 768, "height": 512, "frames": 56, "seed": 42},
        [],
    )

    assert graph["pdd_apply"] == {
        "class_type": "MiniMaxH3PDDAccApply",
        "inputs": {
            "model": ["unet", 0],
            "pdd_file": "MiniMax-H3-FL2VA-Acc-8Step.safetensors",
            "nfe": "8",
            "lora_strength": 1.0,
            "head_strength": 1.0,
            "on_off_grid": "error",
            "partition_check": "error",
        },
    }
    assert graph["sampler_select"]["inputs"]["sampler_name"] == "euler"
    assert graph["guider"]["inputs"]["model"] == ["pdd_apply", 0]
    assert graph["sampler"]["inputs"]["sigmas"] == ["pdd_apply", 1]
    assert "scheduler" not in graph
    assert "turbo_lora" not in graph


def test_pdd_i2v_keeps_first_frame_conditioning():
    graph = build_h3_prompt(
        "h3_pdd_i2v",
        "animate",
        "",
        {"width": 768, "height": 512, "frames": 56, "seed": 7},
        [{"role": "first_frame", "remote_id": "input/first.png"}],
    )

    assert graph["pdd_apply"]["inputs"]["pdd_file"] == "MiniMax-H3-FL2VA-Acc-8Step.safetensors"
    assert graph["conditioner"]["inputs"]["first_frame"] == ["first_frame", 0]
    assert graph["sampler"]["inputs"]["latent_image"] == ["conditioner", 1]


def test_pdd_r2v_pairs_ref2va_model_and_checkpoint():
    graph = build_h3_prompt(
        "h3_pdd_r2v",
        "keep the same subject",
        "",
        {"width": 768, "height": 512, "frames": 56, "seed": 9},
        [{"role": "reference_image", "remote_id": "input/ref.png"}],
    )

    assert graph["unet"]["inputs"]["unet_name"] == "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
    assert graph["pdd_apply"]["inputs"]["pdd_file"] == "MiniMax-H3-Ref2VA-Acc-8Step.safetensors"
    assert graph["guider"]["inputs"]["conditioning"] == ["reference_conditioner", 0]
    assert graph["sampler"]["inputs"]["latent_image"] == ["reference_conditioner", 1]

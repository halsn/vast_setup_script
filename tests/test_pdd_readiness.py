from runtime.worker_readiness import _enabled_features


def test_pdd_feature_requires_node_and_both_release_files(tmp_path):
    comfy = tmp_path / "ComfyUI"
    custom_nodes = comfy / "custom_nodes"
    custom_nodes.mkdir(parents=True)
    (custom_nodes / "ComfyUI-MiniMax-H3-PDD-Acc").mkdir()
    pdd_dir = comfy / "models" / "pdd_acc"
    pdd_dir.mkdir(parents=True)

    runtime = {"features": [], "profile": {}}
    assert "pdd" not in _enabled_features(runtime, custom_nodes)

    (pdd_dir / "MiniMax-H3-FL2VA-Acc-8Step.safetensors").write_bytes(b"x")
    assert "pdd" not in _enabled_features(runtime, custom_nodes)

    (pdd_dir / "MiniMax-H3-Ref2VA-Acc-8Step.safetensors").write_bytes(b"x")
    assert "pdd" in _enabled_features(runtime, custom_nodes)

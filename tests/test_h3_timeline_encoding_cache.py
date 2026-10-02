from __future__ import annotations

import gc
import __future__
import importlib.util
import logging
import math
import random
import sys
import weakref
from math import prod
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_CACHE_PATH = ROOT / "patches" / "timeline-director" / "reference_cache.py"
SPEC = importlib.util.spec_from_file_location("timeline_director_reference_cache", REFERENCE_CACHE_PATH)
assert SPEC is not None and SPEC.loader is not None
reference_cache = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = reference_cache
SPEC.loader.exec_module(reference_cache)
ReferenceEncodingCache = reference_cache.ReferenceEncodingCache


class FakeTensor:
    """Small tensor-shaped byte buffer for cache tests without a GPU runtime."""

    def __init__(self, data, shape, dtype="float32", device="cpu"):
        self.data = bytes(data)
        self.shape = tuple(shape)
        self.dtype = dtype
        self.device = device
        self.layout = "strided"
        self.is_quantized = False
        self.requires_grad = False
        self.copy_to_calls = 0
        self.fail_cpu_copy = False

    def detach(self):
        return self

    def to(self, device=None, *, copy=False, dtype=None):
        if copy and self.fail_cpu_copy:
            raise RuntimeError("simulated CUDA illegal memory access")
        if device is None:
            device = self.device
        if hasattr(device, "type"):
            device = str(device)
        result = FakeTensor(self.data, self.shape, dtype or self.dtype, device)
        result.copy_to_calls = self.copy_to_calls + int(copy)
        return result

    def contiguous(self):
        return self

    def reshape(self, *shape):
        if len(shape) == 1 and isinstance(shape[0], (tuple, list)):
            shape = tuple(shape[0])
        return FakeTensor(self.data, shape, self.dtype, self.device)

    def stride(self):
        result = []
        stride = 1
        for size in reversed(self.shape):
            result.append(stride)
            stride *= size
        return tuple(reversed(result))

    def view(self, dtype):
        return FakeTensor(self.data, (len(self.data),), str(dtype), self.device)

    def numpy(self):
        return memoryview(self.data)

    def numel(self):
        return prod(self.shape)

    def element_size(self):
        return {"float32": 4, "float16": 2, "bfloat16": 2, "int32": 4, "int64": 8, "uint8": 1}[self.dtype]

    def untyped_storage(self):
        return SimpleNamespace(nbytes=lambda: len(self.data))

    def fill_(self, value):
        self.data = bytes([int(value) & 255]) * len(self.data)
        return self

    def clone(self):
        return FakeTensor(self.data, self.shape, self.dtype, self.device)

    def __getitem__(self, _key):
        return self


class FakeTorchDType:
    def __init__(self, value="bfloat16"):
        self.value = value

    def __str__(self):
        return f"torch.{self.value}"


class FakeTorchDevice:
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return self.value


reference_cache.torch = SimpleNamespace(
    Tensor=FakeTensor, uint8="uint8", strided="strided",
    dtype=FakeTorchDType, device=FakeTorchDevice,
)


def _tensor(data, shape, dtype="float32", device="cpu"):
    return FakeTensor(data, shape, dtype, device)


def _model(stage: str, *, patch_id=None, training: bool = False, hooks=None):
    patcher = SimpleNamespace(
        patches_uuid=patch_id or uuid4(), patches={}, forced_hooks=hooks,
        load_device="cpu",
    )
    if stage == "clip_conditioning":
        model_type = type("CLIP", (), {"__module__": "comfy.sd"})
        model = model_type()
        model.patcher = patcher
        cond_type = type("MiniMaxH3TEModel", (), {"__module__": "comfy.text_encoders.minimax"})
        cond_type.modules = lambda self: iter((self, self.transformer))
        model.cond_stage_model = cond_type()
        model.cond_stage_model.training = True
        model.cond_stage_model.transformer = SimpleNamespace(training=training)
        model.cond_stage_model.dtype = "float16"
        model.cond_stage_model.dtypes = {FakeTorchDType("bfloat16")}
        model.cond_stage_model.model_options = {}
        tokenizer_type = type("MiniMaxH3Tokenizer", (), {"__module__": "comfy.text_encoders.minimax"})
        model.tokenizer = tokenizer_type()
        model.tokenizer_options = {}
        model.layer_idx = None
        model.use_clip_schedule = False
        model.apply_hooks_to_conds = None
        return model

    model_type = type("VAE", (), {"__module__": "comfy.sd"})
    model_type.process_input = lambda self, value: value
    model_type.memory_used_encode = lambda self, *args: 0
    model = model_type()
    model.patcher = patcher
    inner_name, inner_module = (
        ("MiniMaxH3AudioVAE", "comfy.ldm.minimax.audio_vae")
        if stage == "audio_vae"
        else ("MiniMaxH3VideoVAE", "comfy.ldm.minimax.vae")
    )
    inner_type = type(inner_name, (), {"__module__": inner_module})
    inner_type.modules = lambda self: iter((self, getattr(self, "child", self)))
    model.first_stage_model = inner_type()
    model.first_stage_model.training = training
    model.audio_sample_rate = 32000
    return model


def test_exact_hit_returns_independent_copy_of_cached_tensor_tree(caplog):
    cache = ReferenceEncodingCache(max_bytes=1024, max_entries=8)
    model = _model("image_vae")
    image = _tensor(bytes.fromhex("0000003e0000003f"), (1, 1, 2, 1))
    calls = 0

    def encode():
        nonlocal calls
        calls += 1
        return {"latent": _tensor(bytes.fromhex("003e003f"), (1, 1, 2, 1), "bfloat16"), "meta": (7, "image")}

    with caplog.at_level(logging.INFO):
        first = cache.get_or_compute("image_vae", model, (image,), {"crop": "disabled"}, encode)
        first["latent"].fill_(8)
        second = cache.get_or_compute("image_vae", model, (image,), {"crop": "disabled"}, encode)

    assert calls == 1
    assert second["latent"].dtype == "bfloat16"
    assert second["latent"].device == "cpu"
    assert second["latent"].data == bytes.fromhex("003e003f")
    assert second["meta"] == (7, "image")
    assert second is not first and second["latent"] is not first["latent"]
    assert "stage=image_vae status=miss" in caplog.text
    assert "stage=image_vae status=hit" in caplog.text
    assert "elapsed_ms=" in caplog.text


def test_input_bytes_shape_dtype_order_and_options_each_invalidate():
    cache = ReferenceEncodingCache(max_bytes=4096, max_entries=32)
    model = _model("video_vae")
    a = _tensor(bytes.fromhex("0100000002000000"), (2,), "int32")
    b = _tensor(bytes.fromhex("0300000004000000"), (2,), "int32")
    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return _tensor(calls.to_bytes(8, "little"), (1,), "int64")

    base = (a, b)
    cache.get_or_compute("video_vae", model, base, {"canvas": (64, 64)}, compute)
    cache.get_or_compute("video_vae", model, (a.clone(), b.clone()), {"canvas": (64, 64)}, compute)
    cache.get_or_compute("video_vae", model, (_tensor(bytes.fromhex("0100000009000000"), (2,), "int32"), b), {"canvas": (64, 64)}, compute)
    cache.get_or_compute("video_vae", model, (_tensor(a.data, (1, 2), "int32"), b), {"canvas": (64, 64)}, compute)
    cache.get_or_compute("video_vae", model, (_tensor(a.data + bytes(8), (2,), "int64"), b), {"canvas": (64, 64)}, compute)
    cache.get_or_compute("video_vae", model, (b, a), {"canvas": (64, 64)}, compute)
    cache.get_or_compute("video_vae", model, base, {"canvas": (96, 64)}, compute)
    cache.get_or_compute(
        "video_vae", model,
        (_tensor(a.data, (2,), "int32", "cuda:0"), b),
        {"canvas": (64, 64)}, compute,
    )

    assert calls == 7


def test_scalar_tensor_content_is_hashed_and_reused():
    cache = ReferenceEncodingCache(max_bytes=1024, max_entries=8)
    model = _model("audio_vae")
    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return _tensor(bytes([calls]), (), "uint8")

    cache.get_or_compute("audio_vae", model, (_tensor(bytes([3]), (), "uint8"),), (), compute)
    cache.get_or_compute("audio_vae", model, (_tensor(bytes([3]), (), "uint8"),), (), compute)
    cache.get_or_compute("audio_vae", model, (_tensor(bytes([4]), (), "uint8"),), (), compute)

    assert calls == 2


def test_oversized_output_is_not_cloned_into_cpu_cache():
    cache = ReferenceEncodingCache(max_bytes=1, max_entries=8)
    model = _model("video_vae")
    frames = _tensor(bytes(8), (1, 2), "float32")
    output = _tensor(bytes(64), (16,), "float32")

    returned = cache.get_or_compute("video_vae", model, (frames,), (), lambda: output)

    assert returned is output
    assert output.copy_to_calls == 0
    assert cache.bytes_used == 0
    assert cache.entry_count == 0


def test_output_copy_runtime_error_is_not_swallowed_as_cache_miss(caplog):
    cache = ReferenceEncodingCache(max_bytes=1024, max_entries=8)
    model = _model("video_vae")
    frames = _tensor(bytes(8), (1, 2), "float32")
    output = _tensor(bytes(8), (2,), "float32")
    output.fail_cpu_copy = True

    with caplog.at_level(logging.INFO):
        with pytest.raises(RuntimeError, match="illegal memory access"):
            cache.get_or_compute("video_vae", model, (frames,), (), lambda: output)

    assert cache.bytes_used == 0
    assert cache.entry_count == 0
    assert "stage=video_vae status=error" in caplog.text


def test_cached_output_restores_original_device_and_dtype():
    cache = ReferenceEncodingCache(max_bytes=1024, max_entries=8)
    model = _model("video_vae")
    frames = _tensor(bytes(range(16)), (1, 2, 2, 2), "float16", "cuda:1")
    calls = 0

    def encode():
        nonlocal calls
        calls += 1
        return _tensor(bytes.fromhex("003c0040"), (1, 2), "float16", "cuda:1")

    first = cache.get_or_compute("video_vae", model, (frames,), (), encode)
    second = cache.get_or_compute("video_vae", model, (frames,), (), encode)

    assert calls == 1
    assert first.device == second.device == "cuda:1"
    assert first.dtype == second.dtype == "float16"
    assert first.data == second.data == bytes.fromhex("003c0040")
    assert first is not second


def test_patch_uuid_change_invalidates_model_encoding():
    cache = ReferenceEncodingCache(max_bytes=1024, max_entries=8)
    model = _model("audio_vae")
    waveform = _tensor(bytes(range(48)), (1, 2, 6))
    calls = 0

    def encode():
        nonlocal calls
        calls += 1
        return _tensor(bytes([calls]), (1,), "uint8")

    cache.get_or_compute("audio_vae", model, (waveform, 32000), {"target_rate": 32000}, encode)
    cache.get_or_compute("audio_vae", model, (waveform, 32000), {"target_rate": 32000}, encode)
    model.patcher.patches_uuid = uuid4()
    cache.get_or_compute("audio_vae", model, (waveform, 32000), {"target_rate": 32000}, encode)

    assert calls == 2


def test_video_vae_encode_settings_invalidate_cached_reference_latents():
    cache = ReferenceEncodingCache(max_bytes=4096, max_entries=16)
    model = _model("video_vae")
    encode_model = model.first_stage_model
    settings = {
        "tiling": False,
        "tile_size": 256,
        "tile_overlap_min": 16,
        "clip_length": 17,
        "token_drop": 3,
    }
    for name, value in settings.items():
        setattr(encode_model, name, value)
    frames = _tensor(bytes(range(32)), (1, 2, 4, 4, 1), "uint8")
    calls = 0

    def encode():
        nonlocal calls
        calls += 1
        return _tensor(bytes([calls]), (1,), "uint8")

    cache.get_or_compute("video_vae", model, (frames,), (), encode)
    cache.get_or_compute("video_vae", model, (frames,), (), encode)
    for name, value in settings.items():
        setattr(encode_model, name, not value if isinstance(value, bool) else value + 1)
        cache.get_or_compute("video_vae", model, (frames,), (), encode)

    assert calls == len(settings) + 1


def test_audio_vae_encode_rate_settings_invalidate_cached_reference_latents():
    cache = ReferenceEncodingCache(max_bytes=4096, max_entries=16)
    model = _model("audio_vae")
    waveform = _tensor(bytes(range(32)), (1, 2, 16), "uint8")
    calls = 0

    def encode():
        nonlocal calls
        calls += 1
        return _tensor(bytes([calls]), (1,), "uint8")

    cache.get_or_compute("audio_vae", model, (waveform,), (), encode)
    cache.get_or_compute("audio_vae", model, (waveform,), (), encode)
    model.first_stage_model.sample_rate = 44100
    cache.get_or_compute("audio_vae", model, (waveform,), (), encode)

    assert calls == 2


def test_prompt_change_reuses_vae_encoding_but_recomputes_clip_conditioning():
    cache = ReferenceEncodingCache(max_bytes=4096, max_entries=16)
    vae = _model("image_vae")
    clip = _model("clip_conditioning")
    resized = _tensor(bytes(range(48)), (1, 2, 2, 3))
    calls = {"vae": 0, "tokenize": 0, "clip": 0}

    def run(prompt):
        latent = cache.get_or_compute(
            "image_vae", vae, (resized,), (),
            lambda: calls.__setitem__("vae", calls["vae"] + 1) or _tensor(b"abcd", (1,), "uint8"),
        )
        ref_items = [{"type": "image", "data": resized}]

        def encode_scheduled():
            calls["tokenize"] += 1
            tokens = {"prompt": prompt, "references": ref_items}
            calls["clip"] += 1
            return {"conditioning": tokens}

        conditioning = cache.get_or_compute(
            "clip_conditioning", clip, (prompt, ref_items),
            {"layer_idx": None, "use_clip_schedule": False, "tokenizer_options": {}},
            encode_scheduled,
        )
        return conditioning

    red = run("a red boat")
    blue = run("a blue boat")
    blue_cached = run("a blue boat")

    assert calls == {"vae": 1, "tokenize": 2, "clip": 2}
    assert red["conditioning"]["prompt"] == "a red boat"
    assert blue["conditioning"]["prompt"] == blue_cached["conditioning"]["prompt"] == "a blue boat"
    assert blue["conditioning"]["references"][0]["data"].data == blue_cached["conditioning"]["references"][0]["data"].data


def test_managed_director_patch_wraps_each_reference_encoding_stage():
    patch = (ROOT / "patches" / "timeline-director" / "two-phase-checkpoints.patch").read_text(encoding="utf-8")

    assert "from .selflift_runtime.reference_cache import REFERENCE_CACHE as _reference_cache" in patch
    for stage in ("image_vae", "video_vae", "audio_vae", "clip_conditioning"):
        assert f'"{stage}"' in patch
    assert "lambda: vae.encode(resized)" in patch
    assert "lambda: vae.encode(frames)" in patch
    assert "lambda: h3_nodes._encode_ref_audio(audio_vae, audio)" in patch
    assert "clip.encode_from_tokens_scheduled(" in patch
    assert "clip.tokenize(prompt, minimax_ref_items=ref_items)" in patch
    assert "\n+    tokens = clip.tokenize(prompt, minimax_ref_items=ref_items)" not in patch
    assert "def _has_forward_hooks(module: object) -> bool:" in patch


def test_patched_director_function_reuses_exact_reference_encodes_across_prompt_edits():
    """Execute the pinned plugin function excerpt with CPU-only runtime doubles."""
    from_fixture = ROOT / "tests" / "fixtures" / "timeline_director_independent_first.py.txt"
    source = from_fixture.read_text(encoding="utf-8")
    namespace = {
        "math": math,
        "h3_nodes": SimpleNamespace(
            CANVAS_MULTIPLE=16,
            FPS=24,
            REF_IMAGE_SHORT_EDGE=512,
            _empty_av_latent=lambda _width, _height, length: (_tensor(b"latent", (1,)), length),
            _resize=lambda image, _width, _height, _crop: image,
            _encode_ref_audio=lambda model, audio: encode_audio(model, audio),
        ),
        "node_helpers": SimpleNamespace(
            conditioning_set_values=lambda condition, values: {
                "conditioning": condition,
                **values,
            }
        ),
        "_reference_cache": ReferenceEncodingCache(max_bytes=4096, max_entries=32),
    }
    code = compile(
        source,
        str(from_fixture),
        "exec",
        flags=__future__.annotations.compiler_flag,
        dont_inherit=True,
    )
    exec(code, namespace)
    execute = namespace["_execute_h3_independent_first"]

    calls = {"image_vae": 0, "audio_vae": 0, "tokenize": 0, "clip": 0}
    vae = _model("image_vae")

    def encode_image(_self, image):
        calls["image_vae"] += 1
        return _tensor(image.data[:8], (1, 2, 2, 2), "bfloat16")

    type(vae).encode = encode_image
    audio_vae = _model("audio_vae")

    def encode_audio(_model, audio):
        calls["audio_vae"] += 1
        return _tensor(audio["waveform"].data[:4], (1, 2), "bfloat16"), 3

    h3_nodes = namespace["h3_nodes"]
    h3_nodes._encode_ref_audio = encode_audio
    clip = _model("clip_conditioning")

    def tokenize(_self, prompt, minimax_ref_items):
        calls["tokenize"] += 1
        return {
            "prompt": prompt,
            "reference_types": [item["type"] for item in minimax_ref_items],
        }

    def encode_scheduled(_self, tokens):
        calls["clip"] += 1
        return {
            "prompt": tokens["prompt"],
            "reference_types": tokens["reference_types"],
            "cond": _tensor(tokens["prompt"].encode("utf-8"), (1,), "uint8"),
        }

    type(clip).tokenize = tokenize
    type(clip).encode_from_tokens_scheduled = encode_scheduled

    first_image = _tensor(bytes(range(64)), (1, 32, 32, 3), "uint8")
    second_image = first_image.clone()
    references = {"image_2": second_image, "image_1": first_image}
    audio = {"waveform": _tensor(bytes(range(32)), (1, 1, 32), "float32"), "sample_rate": 24000}

    def run(prompt, cache):
        namespace["_reference_cache"] = cache
        return execute(
            clip, vae, audio_vae, prompt, 32, 32, 5, "match",
            references, {}, {}, {"audio_1": audio},
        )

    cache = namespace["_reference_cache"]
    red, _ = run("a red boat", cache)
    blue, _ = run("a blue boat", cache)
    blue_hit, _ = run("a blue boat", cache)

    assert calls == {"image_vae": 1, "audio_vae": 1, "tokenize": 2, "clip": 2}
    assert [block["kind"] for block in blue["minimax_refs"]] == ["image", "image", "audio"]
    assert [block["kind"] for block in red["minimax_refs"]] == ["image", "image", "audio"]
    assert blue["conditioning"]["reference_types"] == ["image", "image", "audio"]
    assert [block["latent_h"] for block in blue["minimax_refs"][:2]] == [2, 2]
    assert blue["minimax_refs"][2]["ref_audio_t"] == 3
    assert blue["conditioning"]["prompt"] == "a blue boat"

    fresh_blue, _ = run("a blue boat", ReferenceEncodingCache(max_bytes=4096, max_entries=32))

    def tensor_signature(tensor):
        return tensor.shape, tensor.dtype, tensor.device, tensor.data

    def result_signature(result):
        return (
            result["conditioning"]["prompt"],
            result["conditioning"]["reference_types"],
            tensor_signature(result["conditioning"]["cond"]),
            tuple(
                (block["kind"], block.get("latent_h"), block.get("latent_w"),
                 block.get("ref_audio_t"),
                 tensor_signature(block.get("latent", block.get("audio_latent"))))
                for block in result["minimax_refs"]
            ),
        )

    assert result_signature(blue_hit) == result_signature(fresh_blue)
    assert calls == {"image_vae": 2, "audio_vae": 2, "tokenize": 3, "clip": 3}


def test_clip_options_with_torch_dtype_and_device_still_hit():
    cache = ReferenceEncodingCache(max_bytes=4096, max_entries=16)
    clip = _model("clip_conditioning")
    clip.tokenizer_options = {"min_length": 1, "pad_token": 151643}
    clip.cond_stage_model.model_options = {
        "dtype": FakeTorchDType(), "device": FakeTorchDevice("cuda:0"),
        "model_name": "qwen3vl_32b",
    }
    tokens = {"qwen3vl_32b": [[(151643, 1.0)]]}
    calls = 0

    def encode():
        nonlocal calls
        calls += 1
        return _tensor(bytes([calls]), (1,), "uint8")

    cache.get_or_compute("clip_conditioning", clip, ("a prompt", tokens), (), encode)
    cache.get_or_compute("clip_conditioning", clip, ("a prompt", tokens), (), encode)
    clip.cond_stage_model.model_options["device"] = FakeTorchDevice("cuda:1")
    cache.get_or_compute("clip_conditioning", clip, ("a prompt", tokens), (), encode)
    clip.cond_stage_model.dtypes = {FakeTorchDType("float16")}
    cache.get_or_compute("clip_conditioning", clip, ("a prompt", tokens), (), encode)

    assert calls == 3


def test_unknown_callable_option_bypasses_clip_cache():
    cache = ReferenceEncodingCache(max_bytes=1024, max_entries=8)
    clip = _model("clip_conditioning")
    clip.cond_stage_model.model_options = {"custom_operations": lambda: None}
    calls = 0

    def encode():
        nonlocal calls
        calls += 1
        return _tensor(bytes([calls]), (1,), "uint8")

    cache.get_or_compute("clip_conditioning", clip, ("prompt",), (), encode)
    cache.get_or_compute("clip_conditioning", clip, ("prompt",), (), encode)

    assert calls == 2


def test_unsupported_models_and_hooked_or_training_models_bypass_cache():
    cache = ReferenceEncodingCache(max_bytes=1024, max_entries=8)
    image = _tensor(bytes([1]) * 48, (1, 2, 2, 3))
    safe_model = _model("image_vae")
    unsupported_model = object()
    hooked_model = _model("clip_conditioning", hooks=object())
    training_model = _model("audio_vae", training=True)
    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return _tensor(bytes([calls]), (1,), "uint8")

    for model, stage in (
        (unsupported_model, "image_vae"),
        (hooked_model, "clip_conditioning"),
        (training_model, "audio_vae"),
    ):
        cache.get_or_compute(stage, model, (image,), (), compute)
        cache.get_or_compute(stage, model, (image,), (), compute)

    assert calls == 6


def test_registered_hooks_on_encoder_descendants_bypass_cache():
    cache = ReferenceEncodingCache(max_bytes=1024, max_entries=8)
    image = _tensor(bytes([1]) * 48, (1, 2, 2, 3))
    vae = _model("image_vae")
    vae.first_stage_model.child = SimpleNamespace(_forward_hooks={1: object()})
    clip = _model("clip_conditioning")
    clip.cond_stage_model.transformer._forward_pre_hooks = {1: object()}
    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return _tensor(bytes([calls]), (1,), "uint8")

    for model, stage in ((vae, "image_vae"), (clip, "clip_conditioning")):
        cache.get_or_compute(stage, model, (image,), (), compute)
        cache.get_or_compute(stage, model, (image,), (), compute)

    assert calls == 4


def test_failed_encode_is_not_cached():
    cache = ReferenceEncodingCache(max_bytes=1024, max_entries=8)
    model = _model("image_vae")
    image = _tensor(bytes(12), (1, 1, 1, 3))
    calls = 0

    def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("encode failed")
        return image.clone()

    with pytest.raises(RuntimeError, match="encode failed"):
        cache.get_or_compute("image_vae", model, (image,), (), flaky)
    result = cache.get_or_compute("image_vae", model, (image,), (), flaky)

    assert calls == 2
    assert result.data == image.data


def test_cache_enforces_byte_and_entry_limits():
    cache = ReferenceEncodingCache(max_bytes=32, max_entries=2)
    model = _model("image_vae")

    for value in range(4):
        image = _tensor(value.to_bytes(4, "little"), (1,), "int32")
        cache.get_or_compute(
            "image_vae", model, (image,), (),
            lambda value=value: _tensor(bytes([value]) * 16, (4,), "float32"),
        )

    assert cache.entry_count <= 2
    assert cache.bytes_used <= 32


def test_oversized_result_runs_but_is_not_retained():
    cache = ReferenceEncodingCache(max_bytes=8, max_entries=8)
    model = _model("image_vae")
    image = _tensor(bytes(12), (1, 1, 1, 3))
    calls = 0

    def compute():
        nonlocal calls
        calls += 1
        return _tensor(bytes(32), (8,), "float32")

    cache.get_or_compute("image_vae", model, (image,), (), compute)
    cache.get_or_compute("image_vae", model, (image,), (), compute)

    assert calls == 2
    assert cache.bytes_used == 0


def test_unsafe_training_encode_preserves_rng_progression():
    cache = ReferenceEncodingCache(max_bytes=1024, max_entries=8)
    model = _model("image_vae", training=True)
    image = _tensor(bytes([1]) * 12, (1, 1, 1, 3))
    calls = 0

    def stochastic_encode():
        nonlocal calls
        calls += 1
        return _tensor(repr(random.random()).encode(), (1,), "uint8")

    random.seed(123)
    first = cache.get_or_compute("image_vae", model, (image,), (), stochastic_encode)
    second = cache.get_or_compute("image_vae", model, (image,), (), stochastic_encode)
    random.seed(123)
    expected_first = repr(random.random()).encode()
    expected_second = repr(random.random()).encode()

    assert calls == 2
    assert first.data == expected_first
    assert second.data == expected_second


def test_cache_holds_only_weak_model_identity():
    cache = ReferenceEncodingCache(max_bytes=1024, max_entries=8)
    model = _model("image_vae")
    reference = weakref.ref(model)
    image = _tensor(bytes([1]) * 12, (1, 1, 1, 3))
    cache.get_or_compute("image_vae", model, (image,), (), lambda: image.clone())

    del model
    gc.collect()
    cache.get_or_compute("image_vae", _model("video_vae"), (image,), (), lambda: image.clone())

    assert reference() is None

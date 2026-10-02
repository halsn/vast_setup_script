"""Execute the managed patch on the pinned upstream transition, without a GPU.

The fixture is selflift_runtime/nodes.py lines 653-706 at upstream
309b626973d049b073e93557ff94603efc2d1272. Tensor doubles cover only this
boundary; they do not pretend to validate model inference or video quality.
"""

from __future__ import annotations

import importlib.util
import pickle
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/timeline_selflift_transition.py.txt"
PATCH = ROOT / "patches/timeline-director/two-phase-checkpoints.patch"


class Tensor(float):
    device = "cpu"
    dtype = "float32"

    def to(self, *args, **kwargs):
        return self

    def detach(self):
        return self

    def clone(self):
        return Tensor(self)

    def contiguous(self):
        return self


class Streams(list):
    def to(self, *args, **kwargs):
        return self


def patched_excerpt(tmp_path, fixture_path, first_line, last_line):
    fixture = fixture_path.read_text(encoding="utf-8")
    target = tmp_path / "selflift_runtime/nodes.py"
    target.parent.mkdir()
    target.write_text("\n" * (first_line - 1) + fixture, encoding="utf-8")
    section = PATCH.read_text(encoding="utf-8").split(
        "diff --git a/selflift_runtime/nodes.py b/selflift_runtime/nodes.py\n", 1
    )[1]
    hunks = re.split(r"(?=^@@ )", section, flags=re.MULTILINE)
    selected = []
    for hunk in hunks[1:]:
        start, count = map(int, re.match(r"@@ -(\d+),(\d+)", hunk).groups())
        if first_line <= start and start + count <= last_line + 1:
            selected.append(hunk)
    patch = "--- a/selflift_runtime/nodes.py\n+++ b/selflift_runtime/nodes.py\n" + "".join(selected)
    if not selected:
        return fixture
    applied = subprocess.run(
        ["git", "apply", "--recount", "--whitespace=nowarn", "-"],
        cwd=tmp_path, input=patch, capture_output=True, text=True,
    )
    assert applied.returncode == 0, applied.stderr
    return target.read_text(encoding="utf-8").lstrip("\n")


@pytest.mark.parametrize("stage,checkpoint_id,nested,existing", [
    ("preview", "checkpoint", True, False),
    ("preview", "checkpoint", True, True),
    ("full", "", True, False),
    ("preview", "", True, False),
    ("preview", "checkpoint", False, False),
])
def test_preview_initializes_only_missing_first_segment_av_masks(
    tmp_path, stage, checkpoint_id, nested, existing,
):
    video = SimpleNamespace(shape=(1, 16, 4, 8, 12), device="cpu")
    audio = SimpleNamespace(shape=(1, 8, 2, 32), device="cpu")
    original = {"samples": [video, audio]}
    preserved = [SimpleNamespace(value=0), SimpleNamespace(value=0)]
    if existing:
        original["noise_mask"] = preserved
    namespace = {
        "_streams": lambda value: (value, nested), "_pack": lambda value, nested: value,
        "torch": SimpleNamespace(float32="float32", ones=lambda shape, **kw: SimpleNamespace(shape=shape, value=1)),
        "_validate_latent_input": lambda value: value.get("noise_mask"),
    }
    body = patched_excerpt(tmp_path, ROOT / "tests/fixtures/timeline_selflift_masks.py.txt", 361, 367)
    source = "def run(latent_image, execution_stage, checkpoint_id, highres_tiling=False):\n"
    source += "    w_min, w_max = 0, 1\n    if False:\n" + body + "    return latent_image, noise_masks\n"
    exec(compile(source, "patched_selflift_masks", "exec"), namespace)
    result, masks = namespace["run"](original, stage, checkpoint_id)
    if existing:
        assert result is original and masks is preserved
    elif stage == "preview" and checkpoint_id and nested:
        assert result is not original
        assert "noise_mask" not in original
        assert [m.shape for m in masks] == [(1, 1, 4, 8, 12), (1, 1, 2, 32)]
        assert all(m.value == 1 for m in masks)
        with pytest.raises(ValueError, match="highres_tiling"):
            namespace["run"](original, stage, checkpoint_id, True)
    else:
        assert result is original and masks is None


def run_transition(tmp_path, stage, *, native_av_mask=True):
    spec = importlib.util.spec_from_file_location(
        "checkpoint_store", ROOT / "patches/timeline-director/checkpoint_store.py"
    )
    store = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(store)
    checkpoint_id = str(uuid4())
    latent = {"samples": Streams([Tensor(0), Tensor(7)]), "old_carry": Tensor(1)}
    latent_format = SimpleNamespace(process_out=lambda value: Tensor(value / 2))
    core = SimpleNamespace(
        process_latent_in=lambda values: Streams(Tensor(value * 2) for value in values),
        process_latent_out=lambda values: Streams(Tensor(value / 2) for value in values),
    )
    model = SimpleNamespace(model=core, load_device="cpu")
    management = SimpleNamespace(intermediate_device=lambda: "cpu", intermediate_dtype=lambda: "float32")
    namespace = {
        "comfy": SimpleNamespace(
            sample=SimpleNamespace(prepare_noise=lambda *args: Tensor(1)),
            model_management=management,
        ),
        "torch": SimpleNamespace(where=lambda condition, yes, no: Tensor(yes if condition else no),
                                 zeros_like=lambda value: Tensor(0)),
        "model_sampling": SimpleNamespace(
            noise_scaling=lambda sigma, noise, value: Tensor(sigma * noise + (1 - sigma) * value),
            inverse_noise_scaling=lambda sigma, value: Tensor(value / (1 - sigma)),
        ),
        "_euler_step": lambda state, denoised, start, end: Tensor(state + (state - denoised) / start * (end - start)),
        "_pack": lambda values, nested: Streams(values),
        "_streams": lambda values: (list(values), True),
        "_checkpoint_stream_state": lambda values: {"nested": True, "streams": list(values)},
        "_checkpoint_tensor_payload": pickle.dumps,
        "stage_first_segment": store.stage_first_segment,
        "CheckpointError": store.CheckpointError,
        "folder_paths": SimpleNamespace(get_output_directory=lambda: tmp_path),
        "transition_timer": SimpleNamespace(mark=lambda label: None, finish=lambda: None),
        "log_memory": lambda *args: None,
        "model": model, "high_model": model, "latent_format": latent_format,
        "seed": 1, "sigma_k": Tensor(0.5), "sigma_next": Tensor(0.25),
        "latent_image": latent, "native_av_mask": native_av_mask, "drift_continuation": False,
        "video_anchor": Tensor(0), "audio_streams": [Tensor(7)], "nested": True,
        "m_full": Tensor(1), "auxiliary_masks": [Tensor(0)], "high_noise_mask": Streams([Tensor(1), Tensor(0)]),
        "low_resolution_carry": Tensor(2), "checkpoint_id": checkpoint_id,
        "sampling_fingerprint": "a" * 64, "segment_count": 3, "transition_step": 4,
        "_PREVIOUS_LOW_CARRY_KEY": "old_carry", "_LOW_CARRY_KEY": "low_carry",
    }
    source = "def run(execution_stage, z0_high, auxiliary_next):\n" + patched_excerpt(tmp_path, FIXTURE, 653, 706)
    source += "    return resume_latent, resume_noise\n"
    exec(compile(source, "patched_selflift_transition", "exec"), namespace)
    result = namespace["run"](stage, Tensor(10), [Tensor(7)])
    return result, latent, tmp_path / "timeline_director_checkpoints" / checkpoint_id


def test_preview_captures_clean_vae_endpoint_before_releasing_model_tensor(tmp_path):
    result, original, checkpoint_dir = run_transition(tmp_path, "preview")

    assert result["samples"] == [5, 7]
    assert result["low_carry"] == 2
    assert "old_carry" not in result
    assert "old_carry" in original
    assert original["samples"] == [0, 7]
    state = pickle.loads((checkpoint_dir / "segment-0001.pt").read_bytes())
    assert state["resume_latent"]["streams"] == [0, 7]
    assert state["resume_noise"]["streams"] == [31, -14]
    assert not (checkpoint_dir / "manifest.json").exists()


@pytest.mark.parametrize("native_av_mask", [True, False])
def test_full_transition_keeps_resume_state_without_writing_preview(tmp_path, native_av_mask):
    result, _, checkpoint_dir = run_transition(tmp_path, "full", native_av_mask=native_av_mask)

    assert result[0] == ([0, 7] if native_av_mask else [31 / 6, 14 / 3])
    assert result[1] == ([31, -14] if native_av_mask else [0, 0])
    assert not checkpoint_dir.exists()

"""Exercise the shipped resume validation with H3's compact AV mask shapes.

Only tensor storage and ComfyUI are doubled; the validator comes from the actual
managed patch. Real CPU tensor checks are also run on the deployed instance.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/timeline-director/two-phase-checkpoints.patch"


class Tensor:
    def __init__(self, shape):
        self.shape = tuple(shape)
        self.ndim = len(self.shape)


class CheckpointError(ValueError):
    def __init__(self, message, status_code):
        super().__init__(message)


def resume_validator():
    patch = PATCH.read_text(encoding="utf-8")
    start = patch.index("+def resume_progressive_high_resolution(")
    end = patch.index("+    device = comfy.model_management.intermediate_device()", start)
    source = "\n".join(line[1:] for line in patch[start:end].splitlines() if line.startswith("+"))
    source += "\n    return True\n"
    namespace = {
        "torch": SimpleNamespace(Tensor=Tensor),
        "CheckpointError": CheckpointError,
        "_validate_schedule": lambda *args: None,
        "_validate_sampling": lambda *args: None,
        "_streams": lambda value: (value, True),
        "comfy": SimpleNamespace(sample=SimpleNamespace(fix_empty_latent_channels=lambda model, samples, *args: samples)),
    }
    exec(compile(source, "shipped_checkpoint_resume_validation", "exec"), namespace)
    return namespace["resume_progressive_high_resolution"]


def validate(masks, *, corrupt_stream=None, malformed_structure=False):
    # Representative 1152x640/73-frame segment with reference latents;
    # reproduce the compact native mask seen in the failed segment.
    shapes = [(1, 16, 22, 40, 72), (1, 64, 2, 147)]
    state = {"native_av_mask": True}
    for name in ("resume_latent", "resume_noise"):
        state[name] = {"nested": True, "streams": [Tensor(shape) for shape in shapes]}
    state["high_noise_mask"] = {"nested": True, "streams": [Tensor(shape) for shape in masks]}
    if corrupt_stream:
        name, index, shape = corrupt_stream
        state[name]["streams"][index] = Tensor(shape)
    if malformed_structure:
        state["high_noise_mask"]["nested"] = False
    model = SimpleNamespace(get_model_object=lambda name: None)
    return resume_validator()(model, None, None, {"samples": [Tensor(shape) for shape in shapes]}, None, None, 1, 1, 4, state)


@pytest.mark.parametrize("masks", [
    [(1, 1, 22, 1, 1), (1, 1, 2, 147)],
    [(1, 1, 1, 1, 1), (1, 1, 1, 1)],
    [(1, 1, 22, 40, 72), (1, 1, 2, 147)],
    [(1, 16, 22, 40, 72), (1, 64, 2, 147)],
])
def test_resume_accepts_native_broadcastable_av_masks(masks):
    assert validate(masks) is True


@pytest.mark.parametrize("index,shape", [
    (0, (1, 1, 21, 1, 1)),  # wrong segment duration, not a broadcast axis
    (0, (1, 1, 22, 20, 36)),  # unrelated resolution must not be resized
    (0, (2, 1, 22, 1, 1)),  # batch mismatch
    (0, (1, 3, 22, 40, 72)),  # invalid channel count
    (0, (1, 22, 40, 72)),  # wrong rank
    (1, (1, 1, 2, 146)),  # wrong audio duration
    (1, (1, 3, 2, 147)),  # invalid audio channel count
])
def test_resume_still_rejects_incompatible_masks(index, shape):
    masks = [(1, 1, 22, 40, 72), (1, 1, 2, 147)]
    masks[index] = shape
    with pytest.raises(CheckpointError, match="high_noise_mask tensor shape"):
        validate(masks)


@pytest.mark.parametrize("name", ["resume_latent", "resume_noise"])
def test_resume_latent_and_noise_still_require_exact_shape(name):
    with pytest.raises(CheckpointError, match=f"{name} tensor shape"):
        validate([(1, 1, 22, 1, 1), (1, 1, 2, 147)], corrupt_stream=(name, 0, (1, 16, 22, 20, 36)))


def test_resume_still_rejects_wrong_av_structure():
    with pytest.raises(CheckpointError, match="stream structure"):
        validate([(1, 1, 22, 1, 1), (1, 1, 2, 147)], malformed_structure=True)

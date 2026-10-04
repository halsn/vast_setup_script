"""CPU regression tests for OOM propagation, never a GPU generation job."""
import importlib.util
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("oom_installer", ROOT / "scripts/h3_attention_runtime.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

SOURCE = '''
def attention_sage(q, k, v, heads, mask=None, attn_precision=None, skip_reshape=False, skip_output_reshape=False, **kwargs):
    tensor_layout = "HND"
    try:
        out = sageattn(q, k, v)
    except Exception as e:
        return attention_pytorch(q, k, v, heads)
    return out
'''

# The exact handler installed by the existing v1 managed patch.
LEGACY_HANDLER = '''        if "illegal memory access" in str(e).lower() or "device-side assert" in str(e).lower():
            record_attention("cuda_error", str(e))
            raise
        record_attention("pytorch", str(e))
'''


class OutOfMemoryError(RuntimeError):
    pass


OOMError = OutOfMemoryError


class Tensor:
    def numel(self):
        return 1

    def contiguous(self):
        return self


def execute(monkeypatch, source, failure=None):
    events, fallbacks = [], []
    status = types.ModuleType("comfy.h3_attention_status")
    status.record_attention = lambda *args: events.append(args)
    management = types.ModuleType("comfy.model_management")
    management.OOM_EXCEPTION = OOMError
    monkeypatch.setitem(sys.modules, "comfy.h3_attention_status", status)
    monkeypatch.setitem(sys.modules, "comfy.model_management", management)

    def sage(*args):
        if failure:
            raise failure
        return "sage-result"

    def fallback(*args):
        fallbacks.append(args)
        return "sdpa-result"

    env = {"sageattn": sage, "attention_pytorch": fallback}
    exec(installer.patch_attention_source(source), env)
    return env["attention_sage"], events, fallbacks


@pytest.mark.parametrize("message", [
    "Allocation on device 0 would exceed allowed memory. (out of memory)",
    "CUDA out of memory. Tried to allocate 1.24 GiB",
    "CUDA error: memory allocation",
    "cudaErrorMemoryAllocation",
])
def test_sage_oom_is_reported_without_launching_a_second_attention(monkeypatch, message):
    failure = RuntimeError(message)
    fn, events, fallbacks = execute(monkeypatch, SOURCE, failure)
    with pytest.raises(OOMError, match="out of memory") as caught:
        fn(Tensor(), Tensor(), Tensor(), 1)
    assert caught.value.__cause__ is failure
    assert fallbacks == []
    assert events[-1] == ("oom", message)


def test_native_oom_type_is_preserved_even_without_matching_message(monkeypatch):
    failure = OOMError("allocator exhausted")
    fn, events, fallbacks = execute(monkeypatch, SOURCE, failure)
    with pytest.raises(OOMError) as caught:
        fn(Tensor(), Tensor(), Tensor(), 1)
    assert caught.value is failure
    assert events[-1][0] == "oom"
    assert fallbacks == []


def test_nonfatal_kernel_error_keeps_supported_fallback(monkeypatch):
    fn, events, fallbacks = execute(monkeypatch, SOURCE, RuntimeError("unsupported kernel dtype"))
    assert fn(Tensor(), Tensor(), Tensor(), 1) == "sdpa-result"
    assert len(fallbacks) == 1
    assert events[-1][0] == "pytorch"


def test_existing_v1_handler_is_upgraded_idempotently(monkeypatch):
    old = SOURCE.replace("    tensor_layout", "    from comfy.h3_attention_status import record_attention\n    tensor_layout")
    old = old.replace("    try:", "    # H3_SAGE_LAYOUT_GUARD_V1\n    q, k, v = (tensor.contiguous() for tensor in (q, k, v))\n    try:")
    old = old.replace("    except Exception as e:\n", "    except Exception as e:\n" + LEGACY_HANDLER)
    upgraded = installer.patch_attention_source(old)
    assert "# H3_SAGE_OOM_GUARD_V1" in upgraded
    assert installer.patch_attention_source(upgraded) == upgraded
    fn, _, fallbacks = execute(monkeypatch, upgraded, RuntimeError("CUDA out of memory"))
    with pytest.raises(OOMError):
        fn(Tensor(), Tensor(), Tensor(), 1)
    assert not fallbacks


def test_guard_rejects_duplicate_oom_marker():
    once = installer.patch_attention_source(SOURCE)
    with pytest.raises(ValueError, match="Modified managed Sage OOM guard"):
        installer.patch_attention_source(once + "\n# H3_SAGE_OOM_GUARD_V1\n")


def test_guard_rejects_modified_managed_oom_handler():
    once = installer.patch_attention_source(SOURCE)
    with pytest.raises(ValueError, match="Modified managed Sage OOM guard"):
        installer.patch_attention_source(once.replace('record_attention("oom", str(e))', 'record_attention("pytorch", str(e))'))


def test_runtime_status_counts_oom_separately_from_real_fallbacks():
    env = {}
    exec(installer.STATUS_SOURCE, env)
    env["record_attention"]("oom", "CUDA out of memory")
    assert env["_status"]["oom_count"] == 1
    assert env["_status"]["last_oom_reason"] == "CUDA out of memory"
    assert env["_status"]["fallback_count"] == 0
    assert env["_status"]["last_fallback_reason"] is None
    env["record_attention"]("pytorch", "unsupported dtype")
    assert env["_status"]["fallback_count"] == 1
    assert env["_status"]["oom_count"] == 1

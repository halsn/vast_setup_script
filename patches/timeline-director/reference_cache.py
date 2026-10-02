"""Exact, bounded CPU cache for deterministic MiniMax H3 reference encodes."""

from __future__ import annotations

import hashlib
import logging
import math
import struct
import threading
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from uuid import UUID

try:
    import torch
except ImportError:  # Tests and patch inspection do not require a ComfyUI runtime.
    torch = None


_LOG = logging.getLogger(__name__)
_CACHEABLE_STAGES = {"image_vae", "video_vae", "audio_vae", "clip_conditioning"}
_DEFAULT_MAX_BYTES = 512 * 1024 * 1024
_DEFAULT_MAX_ENTRIES = 48


class _Uncacheable(TypeError):
    pass


@dataclass(frozen=True)
class _StoredTensor:
    tensor: object
    device: str


@dataclass
class _Entry:
    model_ref: weakref.ReferenceType
    value: object
    size: int


def _type_in_mro(value: object, module: str, name: str) -> bool:
    return any(cls.__module__ == module and cls.__name__ == name for cls in type(value).__mro__)


def _is_comfy_vae(model: object) -> bool:
    return type(model).__module__ == "comfy.sd" and type(model).__name__ == "VAE"


def _has_forward_hooks(module: object) -> bool:
    modules = getattr(module, "modules", None)
    if callable(modules):
        try:
            candidates = tuple(modules())
        except Exception:
            return True
    else:
        candidates = (module,)
    for candidate in candidates:
        if any(getattr(candidate, name, None) for name in (
            "_forward_hooks", "_forward_pre_hooks", "_backward_hooks",
        )):
            return True
    return False


def _model_state(model: object, stage: str) -> tuple | None:
    if stage not in _CACHEABLE_STAGES or torch is None:
        return None
    try:
        model_ref = weakref.ref(model)
        patcher = model.patcher
        patches_uuid = patcher.patches_uuid
    except (AttributeError, TypeError):
        return None
    if not isinstance(patches_uuid, UUID) or getattr(patcher, "forced_hooks", object()) is not None:
        return None

    if stage == "clip_conditioning":
        if type(model).__module__ != "comfy.sd" or type(model).__name__ != "CLIP":
            return None
        if getattr(model, "apply_hooks_to_conds", None) is not None:
            return None
        cond = getattr(model, "cond_stage_model", None)
        tokenizer = getattr(model, "tokenizer", None)
        if not _type_in_mro(cond, "comfy.text_encoders.minimax", "MiniMaxH3TEModel"):
            return None
        if not _type_in_mro(tokenizer, "comfy.text_encoders.minimax", "MiniMaxH3Tokenizer"):
            return None
        transformer = getattr(cond, "transformer", None)
        if transformer is None or getattr(transformer, "training", True) is not False:
            return None
        if _has_forward_hooks(cond):
            return None
        if any(name in vars(model) for name in ("tokenize", "encode_from_tokens_scheduled")):
            return None
        if any(name in vars(cond) for name in ("encode_token_weights", "forward")):
            return None
        state = (
            id(model), id(patcher), str(patches_uuid), id(cond), id(transformer), id(tokenizer),
            getattr(model, "layer_idx", None), bool(getattr(model, "use_clip_schedule", False)),
            str(getattr(patcher, "load_device", "")),
            str(getattr(cond, "dtype", "")),
            tuple(sorted(str(dtype) for dtype in getattr(cond, "dtypes", ()))),
            getattr(model, "tokenizer_options", None),
            getattr(cond, "model_options", None),
        )
        return model_ref, state

    if not _is_comfy_vae(model):
        return None
    inner = getattr(model, "first_stage_model", None)
    expected = (
        ("comfy.ldm.minimax.audio_vae", "MiniMaxH3AudioVAE")
        if stage == "audio_vae"
        else ("comfy.ldm.minimax.vae", "MiniMaxH3VideoVAE")
    )
    if (type(inner).__module__, type(inner).__name__) != expected:
        return None
    if getattr(inner, "training", True) is not False:
        return None
    if _has_forward_hooks(inner):
        return None
    if "encode" in vars(model) or "encode" in vars(inner):
        return None
    encode_settings = (
        getattr(inner, "tiling", None),
        getattr(inner, "tile_size", None),
        getattr(inner, "tile_overlap_min", None),
        getattr(inner, "clip_length", None),
        getattr(inner, "token_drop", None),
    )
    if stage == "audio_vae":
        encode_settings += (
            getattr(inner, "sample_rate", None),
            getattr(inner, "hop_length", None),
            getattr(inner, "samples_per_latent", None),
            getattr(inner, "latents_per_second", None),
        )
    state = (
        id(model), id(patcher), str(patches_uuid), id(inner),
        encode_settings,
        str(getattr(model, "device", "")), str(getattr(model, "output_device", "")),
        str(getattr(model, "vae_dtype", "")), str(getattr(model, "vae_output_dtype", lambda: "")()),
        getattr(model, "latent_dim", None), getattr(model, "not_video", None),
        getattr(model, "crop_input", None), _method_identity(getattr(model, "process_input", None)),
        _method_identity(getattr(model, "memory_used_encode", None)),
        getattr(model, "audio_sample_rate", None) if stage == "audio_vae" else None,
    )
    return model_ref, state


def _hash_part(hasher, data: bytes) -> None:
    hasher.update(len(data).to_bytes(8, "little"))
    hasher.update(data)


def _method_identity(value: object) -> int:
    return id(getattr(value, "__func__", value))


def _fingerprint(value: object, hasher) -> None:
    if torch is not None and isinstance(value, torch.Tensor):
        if value.layout != torch.strided or value.is_quantized:
            raise _Uncacheable("unsupported tensor layout")
        if getattr(value, "is_conj", lambda: False)() or getattr(value, "is_neg", lambda: False)():
            raise _Uncacheable("view-bit tensor")
        metadata = (
            tuple(value.shape), str(value.dtype), str(value.device),
            str(value.layout), tuple(value.stride()),
        )
        _hash_part(hasher, b"tensor")
        _hash_part(hasher, repr(metadata).encode("utf-8"))
        try:
            cpu = value.detach().to(device="cpu")
            raw = cpu.contiguous().reshape(-1).view(torch.uint8).numpy()
            hasher.update(memoryview(raw).cast("B"))
        except (TypeError, ValueError, NotImplementedError) as exc:
            raise _Uncacheable("tensor bytes are unavailable") from exc
        return
    if torch is not None:
        dtype_type = getattr(torch, "dtype", ())
        device_type = getattr(torch, "device", ())
        if isinstance(dtype_type, type) and isinstance(value, dtype_type):
            _hash_part(hasher, b"torch.dtype")
            _hash_part(hasher, str(value).encode("ascii"))
            return
        if isinstance(device_type, type) and isinstance(value, device_type):
            _hash_part(hasher, b"torch.device")
            _hash_part(hasher, str(value).encode("ascii"))
            return
    if value is None or type(value) in (bool, int, str, bytes):
        _hash_part(hasher, type(value).__name__.encode("ascii"))
        if isinstance(value, bytes):
            _hash_part(hasher, value)
        else:
            _hash_part(hasher, str(value).encode("utf-8"))
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise _Uncacheable("non-finite option")
        _hash_part(hasher, b"float")
        hasher.update(struct.pack("!d", value))
        return
    if type(value) in (tuple, list):
        _hash_part(hasher, type(value).__name__.encode("ascii"))
        _hash_part(hasher, len(value).to_bytes(8, "little"))
        for item in value:
            _fingerprint(item, hasher)
        return
    if type(value) is dict:
        _hash_part(hasher, b"dict")
        _hash_part(hasher, len(value).to_bytes(8, "little"))
        for key, item in value.items():
            if type(key) not in (str, int, float, bool, bytes, tuple):
                raise _Uncacheable("unsupported mapping key")
            _fingerprint(key, hasher)
            _fingerprint(item, hasher)
        return
    raise _Uncacheable(f"unsupported value type {type(value).__name__}")


def _store_tree(value: object, memo: dict[int, object]) -> tuple[object, int]:
    if torch is not None and isinstance(value, torch.Tensor):
        existing = memo.get(id(value))
        if existing is not None:
            return existing, 0
        if value.layout != torch.strided or value.is_quantized:
            raise _Uncacheable("unsupported output tensor")
        stored = _StoredTensor(value.detach().to(device="cpu", copy=True), str(value.device))
        memo[id(value)] = stored
        return stored, int(stored.tensor.untyped_storage().nbytes())
    if value is None or type(value) in (bool, int, float, str, bytes):
        return value, 0
    if type(value) is list:
        output, size = [], 0
        for item in value:
            cloned, item_size = _store_tree(item, memo)
            output.append(cloned)
            size += item_size
        return output, size
    if type(value) is tuple:
        output, size = [], 0
        for item in value:
            cloned, item_size = _store_tree(item, memo)
            output.append(cloned)
            size += item_size
        return tuple(output), size
    if type(value) is dict:
        output, size = {}, 0
        for key, item in value.items():
            if not _safe_mapping_key(key):
                raise _Uncacheable("unsupported output mapping key")
            cloned, item_size = _store_tree(item, memo)
            output[key] = cloned
            size += item_size
        return output, size
    raise _Uncacheable(f"unsupported output type {type(value).__name__}")


def _safe_mapping_key(value: object) -> bool:
    if type(value) in (str, int, float, bool, bytes):
        return True
    return type(value) is tuple and all(_safe_mapping_key(item) for item in value)


def _tree_size(value: object, seen: set[int]) -> int:
    if torch is not None and isinstance(value, torch.Tensor):
        if value.layout != torch.strided or value.is_quantized:
            raise _Uncacheable("unsupported output tensor")
        if id(value) in seen:
            return 0
        seen.add(id(value))
        return int(value.untyped_storage().nbytes())
    if value is None or type(value) in (bool, int, float, str, bytes):
        return 0
    if type(value) in (list, tuple):
        return sum(_tree_size(item, seen) for item in value)
    if type(value) is dict:
        if any(not _safe_mapping_key(key) for key in value):
            raise _Uncacheable("unsupported output mapping key")
        return sum(_tree_size(item, seen) for item in value.values())
    raise _Uncacheable(f"unsupported output type {type(value).__name__}")


def _restore_tree(value: object, memo: dict[int, object]) -> object:
    if isinstance(value, _StoredTensor):
        existing = memo.get(id(value))
        if existing is None:
            existing = value.tensor.to(device=value.device, copy=True)
            memo[id(value)] = existing
        return existing
    if type(value) is list:
        return [_restore_tree(item, memo) for item in value]
    if type(value) is tuple:
        return tuple(_restore_tree(item, memo) for item in value)
    if type(value) is dict:
        return {key: _restore_tree(item, memo) for key, item in value.items()}
    return value


class ReferenceEncodingCache:
    """LRU cache that stores only bounded CPU tensor copies and weak model IDs."""

    def __init__(self, max_bytes: int = _DEFAULT_MAX_BYTES, max_entries: int = _DEFAULT_MAX_ENTRIES):
        if max_bytes < 0 or max_entries < 0:
            raise ValueError("cache limits must be non-negative")
        self.max_bytes = int(max_bytes)
        self.max_entries = int(max_entries)
        self._entries: OrderedDict[tuple, _Entry] = OrderedDict()
        self._bytes_used = 0
        self._lock = threading.RLock()

    @property
    def bytes_used(self) -> int:
        with self._lock:
            self._discard_dead_models()
            return self._bytes_used

    @property
    def entry_count(self) -> int:
        with self._lock:
            self._discard_dead_models()
            return len(self._entries)

    def _discard_dead_models(self) -> None:
        dead = [key for key, entry in self._entries.items() if entry.model_ref() is None]
        for key in dead:
            self._bytes_used -= self._entries.pop(key).size

    def _insert(self, key: tuple, model_ref: weakref.ReferenceType, value: object, size: int) -> None:
        if size > self.max_bytes or self.max_entries == 0:
            return
        with self._lock:
            self._discard_dead_models()
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes_used -= previous.size
            while self._entries and (
                self._bytes_used + size > self.max_bytes
                or len(self._entries) >= self.max_entries
            ):
                _, evicted = self._entries.popitem(last=False)
                self._bytes_used -= evicted.size
            self._entries[key] = _Entry(model_ref, value, size)
            self._bytes_used += size

    def get_or_compute(self, stage: str, model: object, inputs: object,
                       options: object, compute):
        started = time.perf_counter()
        state = _model_state(model, stage)
        if state is None:
            try:
                result = compute()
            except Exception:
                _LOG.info("H3 reference cache stage=%s status=error elapsed_ms=%.3f", stage, (time.perf_counter() - started) * 1000)
                raise
            _LOG.info("H3 reference cache stage=%s status=bypass elapsed_ms=%.3f", stage, (time.perf_counter() - started) * 1000)
            return result

        model_ref, model_state = state
        try:
            hasher = hashlib.sha256()
            _fingerprint(model_state, hasher)
            _fingerprint(inputs, hasher)
            _fingerprint(options, hasher)
        except _Uncacheable:
            try:
                result = compute()
            except Exception:
                _LOG.info("H3 reference cache stage=%s status=error elapsed_ms=%.3f", stage, (time.perf_counter() - started) * 1000)
                raise
            _LOG.info("H3 reference cache stage=%s status=bypass elapsed_ms=%.3f", stage, (time.perf_counter() - started) * 1000)
            return result
        except Exception:
            _LOG.info("H3 reference cache stage=%s status=error elapsed_ms=%.3f", stage, (time.perf_counter() - started) * 1000)
            raise

        key = (stage, id(model), hasher.digest())
        with self._lock:
            self._discard_dead_models()
            entry = self._entries.get(key)
            if entry is not None and entry.model_ref() is model:
                self._entries.move_to_end(key)
                try:
                    result = _restore_tree(entry.value, {})
                except Exception:
                    _LOG.info("H3 reference cache stage=%s status=error elapsed_ms=%.3f", stage, (time.perf_counter() - started) * 1000)
                    raise
                _LOG.info("H3 reference cache stage=%s status=hit elapsed_ms=%.3f", stage, (time.perf_counter() - started) * 1000)
                return result
            if entry is not None:
                self._bytes_used -= self._entries.pop(key).size

        try:
            result = compute()
        except Exception:
            _LOG.info("H3 reference cache stage=%s status=error elapsed_ms=%.3f", stage, (time.perf_counter() - started) * 1000)
            raise
        try:
            size = _tree_size(result, set())
        except _Uncacheable:
            _LOG.info("H3 reference cache stage=%s status=miss elapsed_ms=%.3f cached=no", stage, (time.perf_counter() - started) * 1000)
            return result
        except Exception:
            _LOG.info("H3 reference cache stage=%s status=error elapsed_ms=%.3f", stage, (time.perf_counter() - started) * 1000)
            raise
        if size > self.max_bytes or self.max_entries == 0:
            _LOG.info("H3 reference cache stage=%s status=miss elapsed_ms=%.3f cached=no bytes=%d", stage, (time.perf_counter() - started) * 1000, size)
            return result
        try:
            stored, size = _store_tree(result, {})
        except _Uncacheable:
            _LOG.info("H3 reference cache stage=%s status=miss elapsed_ms=%.3f cached=no", stage, (time.perf_counter() - started) * 1000)
            return result
        except Exception:
            _LOG.info("H3 reference cache stage=%s status=error elapsed_ms=%.3f", stage, (time.perf_counter() - started) * 1000)
            raise
        self._insert(key, model_ref, stored, size)
        _LOG.info("H3 reference cache stage=%s status=miss elapsed_ms=%.3f cached=%s bytes=%d", stage, (time.perf_counter() - started) * 1000, "yes" if size <= self.max_bytes and self.max_entries else "no", size)
        return result


REFERENCE_CACHE = ReferenceEncodingCache()

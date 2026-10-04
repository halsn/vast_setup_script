"""Install the common Sage layout guard and ComfyUI's live H3 runtime status."""

import argparse
import ast
import json
from pathlib import Path
import textwrap


MARKER = "# H3_SAGE_LAYOUT_GUARD_V1"
OOM_MARKER = "# H3_SAGE_OOM_GUARD_V1"
OOM_HANDLER = '''        # H3_SAGE_OOM_GUARD_V1
        message = str(e).lower()
        if (any(cls.__name__ == "OutOfMemoryError" for cls in type(e).__mro__)
                or any(token in message for token in (
                    "out of memory", "cuda error: memory allocation", "cudaerrormemoryallocation"))):
            from comfy.model_management import OOM_EXCEPTION
            record_attention("oom", str(e))
            if isinstance(e, OOM_EXCEPTION):
                raise
            raise OOM_EXCEPTION(
                "H3 SageAttention out of memory; lower output/reference resolution or reference frames. "
                "No attention fallback was attempted. Original error: " + str(e)
            ) from e
'''
VIDEO_VAES = ("minimax_h3_video_vae_int8_convrot.safetensors", "minimax_h3_video_vae_fp16.safetensors")
STATUS_SOURCE = '''"""Observed attention calls; configured attention is reported separately."""
import importlib.metadata
import time

_status = {"last_backend": None, "last_call_at": None, "fallback_count": 0,
           "last_fallback_reason": None, "layout_guard": "v1", "oom_guard": "v1",
           "oom_count": 0, "last_oom_reason": None}

def record_attention(backend, reason):
    _status.update(last_backend=backend, last_call_at=time.time())
    if backend == "oom":
        _status["oom_count"] += 1
        _status["last_oom_reason"] = str(reason)
    elif reason and backend == "pytorch":
        _status["fallback_count"] += 1
        _status["last_fallback_reason"] = str(reason)

def runtime_status():
    from comfy.ldm.modules import attention
    names = {"attention_sage": "sage", "attention_pytorch": "pytorch",
             "attention_comfy_kitchen_int8": "comfy_kitchen_int8",
             "attention3_sage": "sage3"}
    configured = attention.optimized_attention.__name__
    result = {**_status, "configured_backend": names.get(configured, configured),
              "available_backends": sorted(attention.REGISTERED_ATTENTION_FUNCTIONS),
              "observation_scope": "common SageAttention calls since ComfyUI start"}
    result["versions"] = {}
    for package in ("torch", "triton", "sageattention", "comfy-kitchen", "comfy-aimdo"):
        try:
            result["versions"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result["versions"][package] = None
    return result
'''

NODE_SOURCE = '''from aiohttp import web
from server import PromptServer
from comfy.h3_attention_status import runtime_status

NODE_CLASS_MAPPINGS = {}

@PromptServer.instance.routes.get("/h3/runtime_status")
async def h3_runtime_status(request):
    return web.json_response(runtime_status())
'''


def patch_attention_source(source: str) -> str:
    tree = ast.parse(source)
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "attention_sage"]
    if len(functions) != 1:
        raise ValueError("Expected one ComfyUI attention_sage function")
    function = functions[0]
    required = {"q", "k", "v", "heads", "mask", "attn_precision", "skip_reshape", "skip_output_reshape"}
    if not required.issubset({a.arg for a in function.args.args}):
        raise ValueError("Unsupported ComfyUI attention_sage signature")
    if MARKER in source:
        if source.count(MARKER) != 1 or "q, k, v = (tensor.contiguous() for tensor in (q, k, v))" not in source:
            raise ValueError("Modified managed Sage guard")
        return patch_oom_handler(source, function)
    calls = [n for n in ast.walk(function) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "sageattn"]
    tries = [n for n in ast.walk(function) if isinstance(n, ast.Try)
             and any(call in list(ast.walk(n)) for call in calls)]
    if len(calls) != 1 or len(tries) != 1:
        raise ValueError("Unsupported ComfyUI Sage call structure")
    call = calls[0]
    guarded = tries[0]
    handler = next((n for n in guarded.handlers if n.name == "e"), None)
    if handler is None or call.lineno != call.end_lineno:
        raise ValueError("Unsupported ComfyUI Sage exception structure")
    insertions = {
        function.body[0].lineno: '    from comfy.h3_attention_status import record_attention\n',
        guarded.lineno: textwrap.dedent('''\
            # H3_SAGE_LAYOUT_GUARD_V1
            if any(tensor.numel() >= 2**31 for tensor in (q, k, v)):
                record_attention("pytorch", "Sage int32 element offset limit")
                if tensor_layout == "NHD":
                    q, k, v = (tensor.transpose(1, 2) for tensor in (q, k, v))
                return attention_pytorch(q, k, v, heads, mask=mask, attn_precision=attn_precision,
                                         skip_reshape=True, skip_output_reshape=skip_output_reshape, **kwargs)
            q, k, v = (tensor.contiguous() for tensor in (q, k, v))
        '''),
        call.end_lineno + 1: '        record_attention("sage", None)\n',
        handler.body[0].lineno: textwrap.dedent('''\
            if "illegal memory access" in str(e).lower() or "device-side assert" in str(e).lower():
                record_attention("cuda_error", str(e))
                raise
            record_attention("pytorch", str(e))
        '''),
    }
    insertions[guarded.lineno] = textwrap.indent(insertions[guarded.lineno], "    ")
    insertions[handler.body[0].lineno] = textwrap.indent(insertions[handler.body[0].lineno], "        ")
    # Record deliberate precision/mask fallbacks as well as kernel exceptions.
    for node in ast.walk(function):
        if isinstance(node, ast.Return) and node.lineno < guarded.lineno:
            if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "attention_pytorch":
                indent = " " * node.col_offset
                insertions[node.lineno] = indent + 'record_attention("pytorch", "precision or mask requires PyTorch")\n'
    lines = source.splitlines(keepends=True)
    result = "".join(insertions.get(i, "") + line for i, line in enumerate(lines, 1))
    ast.parse(result)
    patched_function = next(n for n in ast.parse(result).body
                            if isinstance(n, ast.FunctionDef) and n.name == "attention_sage")
    return patch_oom_handler(result, patched_function)


def patch_oom_handler(source: str, function: ast.FunctionDef) -> str:
    """Upgrade only the existing managed Sage exception handler, never retry OOM."""
    handlers = [node for node in ast.walk(function)
                if isinstance(node, ast.ExceptHandler) and node.name == "e"]
    if len(handlers) != 1:
        raise ValueError("Unsupported ComfyUI Sage exception structure")
    handler = handlers[0]
    lines = source.splitlines(keepends=True)
    body = "".join(lines[handler.lineno:handler.end_lineno])
    if OOM_MARKER in source:
        if source.count(OOM_MARKER) != 1 or not body.startswith(OOM_HANDLER):
            raise ValueError("Modified managed Sage OOM guard")
        return source
    expected = '''        if "illegal memory access" in str(e).lower() or "device-side assert" in str(e).lower():
            record_attention("cuda_error", str(e))
            raise
        record_attention("pytorch", str(e))
'''
    if not body.startswith(expected):
        raise ValueError("Modified managed Sage exception handler")
    lines.insert(handler.lineno, OOM_HANDLER)
    result = "".join(lines)
    ast.parse(result)
    return result


def install(comfy_dir: Path, *, video_vae_name: str = VIDEO_VAES[0]) -> None:
    if video_vae_name not in VIDEO_VAES:
        raise ValueError("Unsupported video VAE")
    source_path = comfy_dir / "comfy/ldm/modules/attention.py"
    original = source_path.read_text(encoding="utf-8")
    patched = patch_attention_source(original)
    sources = {
        source_path: patched,
        comfy_dir / "comfy/h3_attention_status.py": STATUS_SOURCE,
        comfy_dir / "custom_nodes/ComfyUI-H3-RuntimeStatus/__init__.py": NODE_SOURCE,
    }
    for path, contents in sources.items():
        compile(contents, str(path), "exec")
    for path, contents in sources.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8") == contents:
            continue
        temporary = path.with_suffix(path.suffix + ".h3-tmp")
        temporary.write_text(contents, encoding="utf-8", newline="\n")
        temporary.replace(path)
    selection = comfy_dir / ".h3-video-vae.json"
    temporary = selection.with_suffix(".json.h3-tmp")
    temporary.write_text(json.dumps({"video_vae": video_vae_name}) + "\n", encoding="utf-8")
    temporary.replace(selection)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comfy-dir", type=Path, required=True)
    parser.add_argument("--video-vae", choices=VIDEO_VAES, default=VIDEO_VAES[0])
    args = parser.parse_args()
    install(args.comfy_dir, video_vae_name=args.video_vae)
    print("[OK] Common Sage layout guard and /h3/runtime_status installed")


if __name__ == "__main__":
    main()

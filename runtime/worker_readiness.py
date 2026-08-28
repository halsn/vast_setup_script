import json
import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping


class ReadinessError(RuntimeError):
    """Raised when the Worker cannot produce a trustworthy readiness payload."""


NODE_FOR_FEATURE = {
    "h3": "ComfyUI-H3-Worker",
    "turbo": "ComfyUI-MiniMax-H3-Turbo",
}


def build_ready_payload(
    runtime_config_path: Path,
    template_catalog_path: Path,
    comfyui_dir: Path,
) -> Dict[str, Any]:
    runtime = _load_object(Path(runtime_config_path), "runtime configuration")
    if runtime.get("status") != "ready":
        raise ReadinessError("runtime is not ready")

    catalog = _load_object(Path(template_catalog_path), "template catalog")
    templates = catalog.get("templates")
    if not isinstance(templates, list):
        raise ReadinessError("template catalog templates must be an array")

    comfyui_root = Path(comfyui_dir)
    custom_nodes = comfyui_root / "custom_nodes"
    workflow_dir = custom_nodes / "ComfyUI-H3-Worker" / "example_workflows"
    available_models = _available_models(runtime.get("models"))
    model_ids = set(available_models)
    features = _enabled_features(runtime, custom_nodes)
    available_templates: List[str] = []
    template_errors: Dict[str, Dict[str, Any]] = {}

    for template in templates:
        if not isinstance(template, dict):
            raise ReadinessError("each template entry must be an object")
        template_id = _required_string(template, "id")
        display_name = str(template.get("display_name") or template_id)
        required_models = _string_list(template.get("required_models"), "required_models")
        template_features = _string_list(template.get("features"), "features")
        workflow = _required_string(template, "workflow")
        missing_models = sorted(model_id for model_id in required_models if model_id not in model_ids)
        missing_features = sorted(
            feature
            for feature in template_features
            if feature in NODE_FOR_FEATURE and feature not in features
        )
        missing_nodes = [
            NODE_FOR_FEATURE[feature]
            for feature in template_features
            if feature in NODE_FOR_FEATURE
            and not (custom_nodes / NODE_FOR_FEATURE[feature]).is_dir()
        ]
        missing_workflow = not (workflow_dir / Path(workflow).name).is_file()

        if missing_models or missing_features or missing_nodes or missing_workflow:
            template_errors[template_id] = _template_error(
                display_name,
                missing_models,
                missing_features,
                missing_nodes,
                missing_workflow,
                workflow,
            )
        else:
            available_templates.append(template_id)

    return {
        "protocol_version": _protocol_version(runtime),
        "status": "ready",
        "worker_id": _worker_id(runtime),
        "comfyui": {
            "version": _comfyui_revision(comfyui_root),
            "api_base": "/comfy",
        },
        "gpu": _gpu_payload(runtime),
        "backend": _required_string(runtime, "backend"),
        "features": features,
        "models": available_models,
        "templates": available_templates,
        "template_errors": template_errors,
    }


def _load_object(path: Path, description: str) -> Dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, ValueError, TypeError) as exc:
        raise ReadinessError(f"could not load {description}") from exc
    if not isinstance(value, dict):
        raise ReadinessError(f"{description} must be an object")
    return value


def _required_string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ReadinessError(f"{key} must be a non-empty string")
    return item.strip()


def _string_list(value: Any, key: str) -> List[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ReadinessError(f"{key} must be an array of non-empty strings")
    return [item.strip() for item in value]


def _available_models(raw_models: Any) -> List[str]:
    if not isinstance(raw_models, list):
        raise ReadinessError("runtime models must be an array")

    available: List[str] = []
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        model_id = raw_model.get("model_id")
        model_path = raw_model.get("path")
        if not isinstance(model_id, str) or not model_id.strip() or not isinstance(model_path, str):
            continue
        if raw_model.get("verified") is not True:
            continue
        path = Path(model_path)
        if not path.is_absolute():
            path = Path(os.getenv("MODEL_CACHE_DIR", "/models")) / path
        try:
            if not path.is_file():
                continue
            expected_size = raw_model.get("size_bytes")
            if isinstance(expected_size, int) and path.stat().st_size != expected_size:
                continue
        except OSError:
            continue
        if model_id.strip() not in available:
            available.append(model_id.strip())
    return available


def _enabled_features(runtime: Mapping[str, Any], custom_nodes: Path) -> List[str]:
    profile = runtime.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    values: List[str] = []
    for source in (runtime.get("features"), profile.get("enabled_features")):
        if isinstance(source, list):
            for feature in source:
                if isinstance(feature, str) and feature.strip() and feature.strip() not in {"h3", "turbo"}:
                    if feature.strip() not in values:
                        values.append(feature.strip())

    if (custom_nodes / NODE_FOR_FEATURE["h3"]).is_dir():
        values.insert(0, "h3")
    if (custom_nodes / NODE_FOR_FEATURE["turbo"]).is_dir():
        values.append("turbo")

    attention = profile.get("attention")
    if isinstance(attention, str) and attention.startswith("sage") and "sageattention" not in values:
        values.append("sageattention")
    if profile.get("convrot") and "convrot" not in values:
        values.append("convrot")
    if profile.get("nvfp4") is True and "nvfp4" not in values:
        values.append("nvfp4")
    return values


def _template_error(
    display_name: str,
    missing_models: List[str],
    missing_features: List[str],
    missing_nodes: List[str],
    missing_workflow: bool,
    workflow: str,
) -> Dict[str, Any]:
    reasons: List[str] = []
    if missing_models:
        reasons.append("missing models: " + ", ".join(missing_models))
    if missing_features:
        reasons.append("missing features: " + ", ".join(missing_features))
    if missing_nodes:
        reasons.append("missing nodes: " + ", ".join(missing_nodes))
    if missing_workflow:
        reasons.append("missing workflow: " + workflow)
    return {
        "message": f"{display_name} is unavailable: " + "; ".join(reasons),
        "missing_models": missing_models,
        "missing_features": missing_features,
        "missing_nodes": missing_nodes,
        "missing_workflow": missing_workflow,
    }


def _protocol_version(runtime: Mapping[str, Any]) -> int:
    value = runtime.get("protocol_version", 1)
    if not isinstance(value, int) or value < 1:
        raise ReadinessError("runtime protocol version is incompatible")
    return value


def _worker_id(runtime: Mapping[str, Any]) -> str:
    configured = runtime.get("worker_id") or os.getenv("H3_WORKER_ID") or socket.gethostname()
    if not isinstance(configured, str) or not configured.strip():
        raise ReadinessError("worker id is unavailable")
    return configured.strip()


def _gpu_payload(runtime: Mapping[str, Any]) -> Dict[str, Any]:
    gpu = runtime.get("gpu")
    if not isinstance(gpu, dict):
        raise ReadinessError("runtime GPU information is unavailable")
    name = gpu.get("name")
    memory = gpu.get("memory_mib", gpu.get("total_memory_mib"))
    architecture = runtime.get("compute_capability")
    if not isinstance(architecture, str):
        capability = gpu.get("compute_capability")
        if isinstance(capability, (list, tuple)) and len(capability) == 2:
            architecture = f"sm{capability[0]}{capability[1]}"
    if not isinstance(name, str) or not name.strip() or not isinstance(architecture, str) or not architecture.strip() or not isinstance(memory, int) or memory <= 0:
        raise ReadinessError("runtime GPU information is incomplete")
    return {"name": name.strip(), "architecture": architecture.strip(), "memory_mib": memory}


def _comfyui_revision(comfyui_dir: Path) -> str:
    if comfyui_dir.is_dir():
        try:
            result = subprocess.run(
                ["git", "-C", str(comfyui_dir), "describe", "--tags", "--always"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            revision = result.stdout.strip()
            if result.returncode == 0 and revision:
                return revision
        except (OSError, subprocess.SubprocessError):
            pass

        for filename in ("comfyui_version.py", "pyproject.toml"):
            path = comfyui_dir / filename
            try:
                content = path.read_text(encoding="utf-8")
            except OSError:
                continue
            match = re.search(r"(?:__version__\s*=|^\s*version\s*=)\s*[\"']([^\"']+)[\"']", content, re.MULTILINE)
            if match:
                return match.group(1)
    return "unknown"

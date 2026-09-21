from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import runpy
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "scripts" / "h3_studio_smoke.py"


def test_h3_studio_smoke_help_is_network_free():
    result = subprocess.run(
        [sys.executable, str(SMOKE), "--help"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "--generate" in result.stdout
    assert "real T2V generation" in result.stdout


def test_h3_studio_smoke_defaults_do_not_generate():
    module = runpy.run_path(str(SMOKE), run_name="h3_studio_smoke_test")
    args = module["parse_args"]([])
    assert args.generate is False
    assert args.timeline_model == "fused"
    assert args.studio_url == "http://127.0.0.1:18080"
    assert args.comfy_url == "http://127.0.0.1:18188"
    assert args.width == 480
    assert args.height == 288
    assert args.duration == 2
    assert args.steps == 4


def test_h3_studio_smoke_rejects_non_h3_canvas_width():
    result = subprocess.run(
        [sys.executable, str(SMOKE), "--width", "481"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "multiple of 32" in result.stderr


def test_h3_studio_smoke_contract_matches_deployed_timeline_alias():
    text = SMOKE.read_text(encoding="utf-8")
    assert 'TIMELINE_SOURCE = "ComfyUI-MiniMaxH3-TimelineDirector"' in text
    assert 'TIMELINE_TEMPLATE = "h3_timeline_director"' in text
    assert 'TIMELINE_SOURCE_UNET = "minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors"' in text
    assert 'TIMELINE_CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"' in text
    assert '"fused": {' in text
    assert '"native": {' in text
    assert '"steps": 8' in text
    assert '"steps": 20' in text
    assert 'REQUIRED_STUDIO_NODES = (' in text
    assert '"MiniMaxH3AudioConditioningT8"' in text
    assert '"MiniMaxH3DualClockSamplerT8"' in text
    assert '"MiniMaxH3AVDecodeT8"' in text
    assert '"MiniMaxH3ReferenceToVideo"' in text
    assert '"VHS_VideoCombine"' in text
    assert 'T8_DIRECTOR_NODE = "MiniMaxH3DirectorProjectT8"' in text
    assert 'T8_DIRECTOR_UI = "/minimax_h3_t8/director/ui"' in text
    assert 'T8_DIRECTOR_WORKBENCH = "/extensions/minimax-h3-audio-T8/director/workbench.mjs"' in text
    assert 'T8_DIRECTOR_CAPABILITIES = "/minimax_h3_t8/director/capabilities"' in text
    assert 'T8_DIRECTOR_SCHEMA = "t8.minimax_h3.director_capabilities.v1"' in text
    assert '"MiniMaxH3TimelinePlanner"' in text
    assert '"MiniMaxH3FiniteSegmentSampler"' in text
    assert '"MiniMaxH3TimelineSelfLiftSampler"' in text
    assert "/api/comfyui/status" in text
    assert "/workflow_templates" in text



@contextmanager
def _serve_get_routes(routes: dict[str, tuple[str, bytes]]):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            route = routes.get(self.path)
            if route is None:
                self.send_response(404)
                self.end_headers()
                return
            content_type, body = route
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize("timeline_model", ["fused", "native"])
def test_h3_studio_smoke_read_only_contract_against_fake_services(timeline_model):
    module = runpy.run_path(str(SMOKE), run_name="h3_studio_smoke_contract_test")
    check_contract = module["check_contract"]
    variants = module["TIMELINE_VARIANTS"]
    timeline_source = module["TIMELINE_SOURCE"]
    timeline_template = module["TIMELINE_TEMPLATE"]
    timeline_clip = module["TIMELINE_CLIP"]
    required_studio_nodes = module["REQUIRED_STUDIO_NODES"]
    required_timeline_nodes = module["REQUIRED_TIMELINE_NODES"]
    t8_workbench = module["T8_DIRECTOR_WORKBENCH"]

    variant = variants[timeline_model]
    timeline_unet = variant["unet"]
    steps = variant["steps"]

    object_info = {
        **{name: {} for name in required_studio_nodes},
        "MiniMaxH3DirectorProjectT8": {},
        **{name: {} for name in required_timeline_nodes},
        "UNETLoader": {
            "input": {
                "required": {
                    "unet_name": [[
                        variants["fused"]["unet"],
                        variants["native"]["unet"],
                    ]]
                }
            }
        },
        "CLIPLoader": {
            "input": {"required": {"clip_name": [[timeline_clip]]}}
        },
    }
    timeline_workflow = {
        "nodes": [
            {"type": "UNETLoader", "widgets_values": [timeline_unet]},
            {"type": "CLIPLoader", "widgets_values": [timeline_clip]},
            {
                "type": "BasicScheduler",
                "widgets_values": ["simple", steps],
                "widgets_values_named": {"steps": steps},
            },
        ]
    }

    comfy_routes = {
        "/object_info": ("application/json", _json_bytes(object_info)),
        "/minimax_h3_t8/director/ui": (
            "text/html; charset=utf-8",
            "<html><title>曜石导演台</title></html>".encode("utf-8"),
        ),
        t8_workbench: (
            "text/javascript; charset=utf-8",
            b"export function createDirectorWorkbench() {}",
        ),
        "/minimax_h3_t8/director/capabilities": (
            "application/json",
            _json_bytes({
                "schema": "t8.minimax_h3.director_capabilities.v1",
                "capabilities": [{"id": "long_video", "state": "ready"}],
            }),
        ),
        "/workflow_templates": (
            "application/json",
            _json_bytes({timeline_source: [timeline_template]}),
        ),
        f"/api/workflow_templates/{timeline_source}/{timeline_template}.json": (
            "application/json",
            _json_bytes(timeline_workflow),
        ),
    }
    studio_routes = {
        "/": ("text/html; charset=utf-8", b"<html>H3 Studio</html>"),
        "/api/comfyui/status": (
            "application/json",
            _json_bytes({"up": True}),
        ),
    }

    with _serve_get_routes(comfy_routes) as comfy_url:
        with _serve_get_routes(studio_routes) as studio_url:
            check_contract(
                studio_url,
                comfy_url,
                timeline_model=timeline_model,
            )

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
T8_DIRECTOR_WEBUI_FIX = ROOT / "scripts" / "h3_t8_director_webui.py"


def test_t8_director_workbench_asset_path_is_repaired_idempotently(tmp_path):
    page = tmp_path / "index.html"
    page.write_text(
        '<script>import("/extensions/minimax-h3-audio-T8/director/workbench.mjs")</script>',
        encoding="utf-8",
    )

    for _ in range(2):
        result = subprocess.run(
            [sys.executable, str(T8_DIRECTOR_WEBUI_FIX), str(page)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        html = page.read_text(encoding="utf-8")
        assert "/extensions/comfyui-minimax-h3-audio-T8/director/workbench.mjs" in html
        assert "/extensions/minimax-h3-audio-T8/director/workbench.mjs" not in html


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
    assert 'T8_DIRECTOR_CAPABILITIES = "/minimax_h3_t8/director/capabilities"' in text
    assert 'T8_DIRECTOR_SCHEMA = "t8.minimax_h3.director_capabilities.v1"' in text
    assert 'T8_STUDIO_ENGINE_CAPABILITIES = ("long_video", "prompt_relay")' in text
    assert 'T8_TEMPLATE_SOURCE = "comfyui-minimax-h3-audio-T8"' in text
    assert 'T8_LONG_VIDEO_TEMPLATE = "h3_t8_long_video_relay"' in text
    assert 'T8_LONG_VIDEO_SMOKE_TEMPLATE = "h3_t8_long_video_relay_smoke"' in text
    assert '"MiniMaxH3LongVideoInNodeLoopEffectsT8Advanced"' in text
    assert '"MiniMaxH3PromptRelayPlanT8Advanced"' in text
    assert '"MiniMaxH3TimelinePlanner"' in text
    assert '"MiniMaxH3FiniteSegmentSampler"' in text
    assert '"MiniMaxH3TimelineSelfLiftSampler"' in text
    assert '"MiniMaxH3TimelineCheckpointCommit"' in text
    assert 'REQUIRED_TWO_PHASE_INPUTS = (' in text
    assert '"sampling_fingerprint"' in text
    assert "/minimax_h3_timeline/checkpoints/not-a-uuid?" in text
    assert "/api/comfyui/status" in text
    assert "/workflow_templates" in text



@contextmanager
def _serve_get_routes(
    routes: dict[str, tuple[str, bytes] | tuple[str, bytes, int]],
    seen_paths: list[str] | None = None,
):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if seen_paths is not None:
                seen_paths.append(self.path)
            route = routes.get(self.path)
            if route is None:
                self.send_response(404)
                self.end_headers()
                return
            content_type, body = route[:2]
            status = route[2] if len(route) == 3 else 200
            self.send_response(status)
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


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "missing_commit",
        "missing_commit_input",
        "wrong_commit_outputs",
        "missing_finite_input",
        "missing_selflift_input",
        "missing_route",
        "wrong_route_status",
        "wrong_route_error",
        "non_json_route",
    ],
)
def test_deployment_timeline_check_requires_gui_two_phase_contract(defect):
    # Exercise the Python checker that the Bash deployment actually executes.
    setup = (ROOT / "scripts" / "setupp_h3_studio.sh").read_text(encoding="utf-8")
    function = setup.split("h3_studio_verify_timeline_director() {", 1)[1]
    checker = function.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
    phase_inputs = {
        "execution_stage": [["full", "preview", "finalize"], {"default": "full"}],
        "checkpoint_id": ["STRING", {"default": ""}],
        "sampling_fingerprint": ["STRING", {"default": ""}],
    }
    catalog = {
        "MiniMaxH3TimelinePlanner": {},
        "MiniMaxH3FiniteSegmentSampler": {
            "input": {"required": {}, "optional": dict(phase_inputs)},
        },
        "MiniMaxH3TimelineSelfLiftSampler": {
            "input": {"required": {}, "optional": dict(phase_inputs)},
        },
        "MiniMaxH3TimelineCheckpointCommit": {
            "input": {"required": {
                "latent": ["LATENT"],
                "checkpoint_id": ["STRING"],
                "fingerprint": ["STRING"],
                "segment_count": ["INT"],
            }},
            "output": ["LATENT"],
        },
        "UNETLoader": {"input": {"required": {"unet_name": [["test-unet.safetensors"]]}}},
        "CLIPLoader": {"input": {"required": {"clip_name": [["test-clip.safetensors"]]}}},
    }
    if defect == "missing_commit":
        del catalog["MiniMaxH3TimelineCheckpointCommit"]
    elif defect == "missing_commit_input":
        del catalog["MiniMaxH3TimelineCheckpointCommit"]["input"]["required"]["fingerprint"]
    elif defect == "wrong_commit_outputs":
        catalog["MiniMaxH3TimelineCheckpointCommit"]["output"] = []
    elif defect == "missing_finite_input":
        del catalog["MiniMaxH3FiniteSegmentSampler"]["input"]["optional"]["execution_stage"]
    elif defect == "missing_selflift_input":
        del catalog["MiniMaxH3TimelineSelfLiftSampler"]["input"]["optional"]["checkpoint_id"]

    probe = "/minimax_h3_timeline/checkpoints/not-a-uuid?fingerprint=" + "0" * 64 + "&segment_count=1"
    probe_body = {"status": "invalid", "error": "Invalid checkpoint ID"}
    if defect == "wrong_route_error":
        probe_body["error"] = "Unrelated validation failure"
    routes = {
        "/object_info": ("application/json", _json_bytes(catalog)),
        "/workflow_templates": ("application/json", _json_bytes({"test-source": ["test-alias"]})),
        "/api/workflow_templates/test-source/test-alias.json": (
            "application/json",
            _json_bytes({"nodes": [
                {"type": "UNETLoader", "widgets_values": ["test-unet.safetensors"]},
                {"type": "CLIPLoader", "widgets_values": ["test-clip.safetensors"]},
                {"type": "BasicScheduler", "widgets_values_named": {"steps": 8}},
            ]}),
        ),
        probe: (
            "application/json",
            b"<html>Bad Request</html>" if defect == "non_json_route" else _json_bytes(probe_body),
            200 if defect == "wrong_route_status" else 400,
        ),
    }
    if defect == "missing_route":
        del routes[probe]
    seen_paths = []
    with _serve_get_routes(routes, seen_paths) as url:
        result = subprocess.run(
            [sys.executable, "-", url + "/object_info", url + "/workflow_templates",
             url + "/api/workflow_templates", "test-source", "test-alias",
             "test-unet.safetensors", "test-clip.safetensors", "8", "upstream-unet.safetensors"],
            input=checker,
            capture_output=True,
            text=True,
        )
    if defect is None:
        assert result.returncode == 0, result.stderr
        assert probe in seen_paths
        assert "[OK]" in result.stderr
    else:
        assert result.returncode != 0, f"Deployment incorrectly accepted {defect}: {result.stderr}"
        assert "[ERROR]" in result.stderr


@pytest.mark.parametrize("timeline_model", ["fused", "native"])
@pytest.mark.parametrize("defect", [None, "commit_input", "commit_output", "route_error"])
def test_h3_studio_smoke_read_only_contract_against_fake_services(timeline_model, defect):
    module = runpy.run_path(str(SMOKE), run_name="h3_studio_smoke_contract_test")
    check_contract = module["check_contract"]
    variants = module["TIMELINE_VARIANTS"]
    timeline_source = module["TIMELINE_SOURCE"]
    timeline_template = module["TIMELINE_TEMPLATE"]
    timeline_clip = module["TIMELINE_CLIP"]
    t8_template_source = module["T8_TEMPLATE_SOURCE"]
    t8_long_video_template = module["T8_LONG_VIDEO_TEMPLATE"]
    t8_long_video_smoke_template = module["T8_LONG_VIDEO_SMOKE_TEMPLATE"]
    t8_long_video_unet = module["T8_LONG_VIDEO_UNET"]
    t8_long_video_clip = module["T8_LONG_VIDEO_CLIP"]
    required_t8_long_video_nodes = module["REQUIRED_T8_LONG_VIDEO_NODES"]
    required_studio_nodes = module["REQUIRED_STUDIO_NODES"]
    required_timeline_nodes = module["REQUIRED_TIMELINE_NODES"]
    two_phase_inputs = module["REQUIRED_TWO_PHASE_INPUTS"]

    variant = variants[timeline_model]
    timeline_unet = variant["unet"]
    steps = variant["steps"]

    object_info = {
        **{name: {} for name in required_studio_nodes},
        "MiniMaxH3DirectorProjectT8": {},
        **{name: {} for name in required_t8_long_video_nodes},
        **{name: {} for name in required_timeline_nodes},
        "MiniMaxH3FiniteSegmentSampler": {
            "input": {"required": {}, "optional": {name: [] for name in two_phase_inputs}}
        },
        "MiniMaxH3TimelineSelfLiftSampler": {
            "input": {"required": {}, "optional": {name: [] for name in two_phase_inputs}}
        },
        "MiniMaxH3TimelineCheckpointCommit": {
            "input": {"required": {
                "latent": ["LATENT"], "checkpoint_id": ["STRING"],
                "fingerprint": ["STRING"], "segment_count": ["INT"],
            }},
            "output": ["LATENT"],
        },
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
    if defect == "commit_input":
        del object_info["MiniMaxH3TimelineCheckpointCommit"]["input"]["required"]["fingerprint"]
    elif defect == "commit_output":
        object_info["MiniMaxH3TimelineCheckpointCommit"]["output"] = []
    t8_workflow = {
        "nodes": [
            {"type": "UNETLoader", "widgets_values": [t8_long_video_unet]},
            {"type": "CLIPLoader", "widgets_values": [t8_long_video_clip]},
            {"type": "MiniMaxH3PromptRelayPlanT8Advanced", "widgets_values": ["g", "a\\nb", 720]},
            {"type": "MiniMaxH3LongVideoInNodeLoopEffectsT8Advanced", "widgets_values": [
                "h3_in_node_relay_eav_stock20_demo", 30.0, 736, 416, 124, 22,
                "", "", "apply_exp", 256, "apply_exp", 4.0, 0.0, 1.0, 32, 1.5,
                512, 123456789, "increment", 20
            ]},
        ]
    }
    t8_smoke_workflow = {
        "nodes": [
            {"type": "UNETLoader", "widgets_values": [t8_long_video_unet]},
            {"type": "CLIPLoader", "widgets_values": [t8_long_video_clip]},
            {"type": "MiniMaxH3PromptRelayPlanT8Advanced", "widgets_values": ["g", "a\\nb", 192]},
            {"type": "MiniMaxH3LongVideoInNodeLoopEffectsT8Advanced", "widgets_values": [
                "h3_t8_relay_smoke_8s", 8.0, 512, 288, 124, 22,
                "", "", "apply_exp", 256, "apply_exp", 4.0, 0.0, 1.0, 32, 1.5,
                512, 123456789, "increment", 20
            ]},
        ]
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

    comfy_paths: list[str] = []
    comfy_routes = {
        "/object_info": ("application/json", _json_bytes(object_info)),
        "/minimax_h3_timeline/checkpoints/not-a-uuid?fingerprint=" + "0" * 64 + "&segment_count=1": (
            "application/json",
            _json_bytes({"status": "invalid", "error": "Unrelated error" if defect == "route_error" else "Invalid checkpoint ID"}),
            400,
        ),
        "/minimax_h3_t8/director/ui": (
            "text/html; charset=utf-8",
            (
                '<html><title>曜石导演台</title><script type="module">'
                'import("/extensions/comfyui-minimax-h3-audio-T8/director/workbench.mjs")'
                "</script></html>"
            ).encode("utf-8"),
        ),
        "/minimax_h3_t8/director/capabilities": (
            "application/json",
            _json_bytes({
                "schema": "t8.minimax_h3.director_capabilities.v1",
                "capabilities": [
                    {"id": "long_video", "state": "ready"},
                    {"id": "prompt_relay", "state": "ready"},
                ],
            }),
        ),
        "/extensions/comfyui-minimax-h3-audio-T8/director/workbench.mjs": (
            "text/javascript",
            b"export const directorWorkbench = true;",
        ),
        "/workflow_templates": (
            "application/json",
            _json_bytes({
                t8_template_source: [t8_long_video_template, t8_long_video_smoke_template],
                timeline_source: [timeline_template],
            }),
        ),
        f"/api/workflow_templates/{t8_template_source}/{t8_long_video_template}.json": (
            "application/json",
            _json_bytes(t8_workflow),
        ),
        f"/api/workflow_templates/{t8_template_source}/{t8_long_video_smoke_template}.json": (
            "application/json",
            _json_bytes(t8_smoke_workflow),
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

    with _serve_get_routes(comfy_routes, comfy_paths) as comfy_url:
        with _serve_get_routes(studio_routes) as studio_url:
            if defect is None:
                check_contract(studio_url, comfy_url, timeline_model=timeline_model)
            else:
                with pytest.raises(RuntimeError, match="checkpoint"):
                    check_contract(studio_url, comfy_url, timeline_model=timeline_model)
    if defect is not None:
        return
    assert "/extensions/comfyui-minimax-h3-audio-T8/director/workbench.mjs" in comfy_paths
    assert any(path.startswith("/minimax_h3_timeline/checkpoints/not-a-uuid?") for path in comfy_paths)

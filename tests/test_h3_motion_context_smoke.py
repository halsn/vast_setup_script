"""Offline tests for the read-only Motion Context preflight and media verifier."""

from contextlib import contextmanager
import importlib.util
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from threading import Thread

import pytest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "scripts" / "h3_motion_context_smoke.py"
WORKFLOW_TOOL = ROOT / "scripts" / "h3_motion_context_workflow.py"
WORKFLOW_SOURCE = ROOT / "tests" / "fixtures" / "h3_motion_context_workflow.json"
SOURCE = "ComfyUI-H3-Motion-Context"
TEMPLATE = "h3_motion_context_smoke"
SMOKE_TOOL_REV = "1b74b968563b507e25fa4c70883648e02ccbd2b9"
WORKFLOW_PATH = f"/api/workflow_templates/{SOURCE}/{TEMPLATE}.json"
NODE_TYPES = (
    "MiniMaxH3MotionContext",
    "MiniMaxH3MotionContextTrim",
    "MiniMaxH3MotionContextSaveLatent",
    "MiniMaxH3MotionContextLoadLatent",
    "MiniMaxH3MotionContextChain",
    "MiniMaxH3MotionContextSeamProbe",
)
UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"


def _load_tool():
    assert TOOL.is_file(), f"expected CLI implementation at {TOOL}"
    spec = importlib.util.spec_from_file_location("h3_motion_context_smoke", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _workflow():
    spec = importlib.util.spec_from_file_location("h3_motion_context_workflow", WORKFLOW_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prepare_workflow(json.loads(WORKFLOW_SOURCE.read_text(encoding="utf-8")))


def _retarget_input(workflow, target, input_name, source, output_name):
    target_slot = next(slot for slot in target["inputs"] if slot["name"] == input_name)
    link_id = target_slot["link"]
    link = next(link for link in workflow["links"] if link[0] == link_id)
    old_source = next(node for node in workflow["nodes"] if node["id"] == link[1])
    old_output = old_source["outputs"][link[2]]
    new_output = next(index for index, slot in enumerate(source["outputs"])
                      if slot["name"] == output_name)
    old_output["links"].remove(link_id)
    source["outputs"][new_output]["links"].append(link_id)
    link[1], link[2] = source["id"], new_output


def _object_info():
    result = {name: {} for name in NODE_TYPES}
    result["UNETLoader"] = {"input": {"required": {"unet_name": [[UNET]]}}}
    result["CLIPLoader"] = {"input": {"required": {"clip_name": [[CLIP]]}}}
    return result


@contextmanager
def fake_comfy(*, object_info=None, templates=None, workflow="default"):
    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self.server.events.append(("GET", self.path))
            if self.path == "/object_info":
                self._respond(200, self.server.object_info)
            elif self.path == "/workflow_templates":
                self._respond(200, self.server.templates)
            elif self.path == WORKFLOW_PATH:
                if self.server.workflow is None:
                    self._respond(404, {"error": "missing workflow"})
                else:
                    self._respond(200, self.server.workflow)
            else:
                self._respond(404, {"error": "not found"})

        def do_POST(self):
            self.server.events.append(("POST", self.path))
            self._respond(405, {"error": "GET only"})

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.events = []
    server.object_info = object_info if object_info is not None else _object_info()
    server.templates = templates if templates is not None else {SOURCE: [TEMPLATE]}
    server.workflow = _workflow() if workflow == "default" else workflow
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _preflight(server):
    return subprocess.run(
        [sys.executable, str(TOOL), "preflight", "--comfy-url",
         f"http://127.0.0.1:{server.server_port}"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_help_is_offline_and_documents_the_manual_restart_protocol():
    result = subprocess.run([sys.executable, str(TOOL), "--help"], capture_output=True,
                            text=True, check=False)
    assert result.returncode == 0, result.stderr
    help_text = result.stdout
    for required in (
        "Chain defaults to 2",
        "0 is unbounded",
        "Do not use Chain for the restart test",
        "one Run/Re-roll at Load 0 / Save 1",
        "verify-latent",
        "restart ComfyUI",
        "one Run/Re-roll at Load 1 / Save 2",
        "join-verify",
        "human visual/audio seam check",
    ):
        assert required in help_text


def test_preflight_reads_only_and_checks_the_installed_smoke_template():
    with fake_comfy() as server:
        result = _preflight(server)
    assert result.returncode == 0, result.stderr
    assert "GET-only" in result.stdout
    assert [method for method, _path in server.events] == ["GET", "GET", "GET"]
    assert [path for _method, path in server.events] == [
        "/object_info", "/workflow_templates", WORKFLOW_PATH,
    ]
    assert not any(path == "/prompt" or path.startswith("/prompt?")
                   for _method, path in server.events)


@pytest.mark.parametrize(
    ("case", "expected_message"),
    (("missing-node", "MiniMaxH3MotionContext"),
     ("missing-template", "template"),
     ("missing-workflow", "workflow")),
)
def test_preflight_reports_missing_node_template_or_workflow(case, expected_message):
    object_info = _object_info()
    templates = {SOURCE: [TEMPLATE]}
    workflow = _workflow()
    if case == "missing-node":
        object_info.pop(NODE_TYPES[0])
    elif case == "missing-template":
        templates[SOURCE] = []
    else:
        workflow = None
    with fake_comfy(object_info=object_info, templates=templates, workflow=workflow) as server:
        result = _preflight(server)
    assert result.returncode != 0
    assert expected_message.lower() in result.stderr.lower()
    assert all(method == "GET" for method, _path in server.events)
    assert not any(path.startswith("/prompt") for _method, path in server.events)


@pytest.mark.parametrize(
    ("mutation", "expected_message"),
    (("wrong-frame-count", "73"),
     ("missing-seam-link", "clip_a_latent"),
     ("broken-subgraph-link", "missing node endpoint"),
     ("wrong-subgraph-link-type", "type"),
     ("invalid-subgraph-input-slot", "slot"),
     ("invalid-subgraph-output-slot", "slot"),
     ("missing-contract-node", NODE_TYPES[2]),
     ("bypass-motion-context", "MotionContext.conditioning"),
     ("bypass-trim-images", "MotionContextTrim.images"),
     ("save-untrimmed-latent", "MotionContextSaveLatent.latent")),
)
def test_preflight_rejects_a_workflow_that_breaks_the_smoke_contract(mutation, expected_message):
    workflow = _workflow()
    if mutation == "wrong-frame-count":
        node = next(n for n in workflow["nodes"] if n["type"] == "MiniMaxH3ImageToVideo")
        node["widgets_values"][-1] = 72
        node["widgets_values_named"]["length"] = 72
    elif mutation.startswith(("broken-subgraph", "wrong-subgraph", "invalid-subgraph")):
        subgraph = next(d for d in workflow["definitions"]["subgraphs"]
                        if d["name"] == "Sampling/Decoding/Create")
        if mutation == "broken-subgraph-link":
            subgraph["links"][0]["origin_id"] = 999999
        elif mutation == "wrong-subgraph-link-type":
            subgraph["links"][0]["type"] = "BROKEN"
        elif mutation == "invalid-subgraph-input-slot":
            link = next(link for link in subgraph["links"]
                        if link["origin_id"] == subgraph["inputNode"]["id"])
            link["origin_slot"] = len(subgraph["inputs"])
        else:
            link = next(link for link in subgraph["links"]
                        if link["target_id"] == subgraph["outputNode"]["id"])
            link["target_slot"] = len(subgraph["outputs"])
    elif mutation == "missing-seam-link":
        probe = next(n for n in workflow["nodes"]
                     if n["type"] == "MiniMaxH3MotionContextSeamProbe")
        load = next(n for n in workflow["nodes"]
                    if n["type"] == "MiniMaxH3MotionContextLoadLatent")
        probe_input = next(i for i in probe["inputs"] if i["name"] == "clip_a_latent")
        link_id = probe_input["link"]
        probe_input["link"] = None
        workflow["links"] = [link for link in workflow["links"] if link[0] != link_id]
        next(o for o in load["outputs"] if o["name"] == "LATENT")["links"].remove(link_id)
    else:
        if mutation == "missing-contract-node":
            workflow["nodes"] = [n for n in workflow["nodes"] if n["type"] != NODE_TYPES[2]]
        elif mutation == "bypass-motion-context":
            fl2va = next(n for n in workflow["nodes"] if n["type"] == "MiniMaxH3ImageToVideo")
            sampler = next(n for n in workflow["nodes"]
                           if any(i["name"] == "model" for i in n.get("inputs", []))
                           and any(o["name"] == "AUDIO" for o in n.get("outputs", [])))
            _retarget_input(workflow, sampler, "conditioning", fl2va, "positive")
        elif mutation == "bypass-trim-images":
            trim = next(n for n in workflow["nodes"] if n["type"] == NODE_TYPES[1])
            sampler = next(n for n in workflow["nodes"]
                           if any(i["name"] == "model" for i in n.get("inputs", []))
                           and any(o["name"] == "AUDIO" for o in n.get("outputs", [])))
            _retarget_input(workflow, sampler, "images", sampler, "IMAGE")
        else:
            save = next(n for n in workflow["nodes"] if n["type"] == NODE_TYPES[2])
            fl2va = next(n for n in workflow["nodes"] if n["type"] == "MiniMaxH3ImageToVideo")
            _retarget_input(workflow, save, "latent", fl2va, "LATENT")
    with fake_comfy(workflow=workflow) as server:
        result = _preflight(server)
    assert result.returncode != 0
    assert expected_message.lower() in result.stderr.lower()
    assert [path for _method, path in server.events] == [
        "/object_info", "/workflow_templates", WORKFLOW_PATH,
    ]
    assert all(method == "GET" for method, _path in server.events)


def test_verify_latent_accepts_a_nonempty_explicit_file(tmp_path):
    latent = tmp_path / "segment-1.latent"
    latent.write_bytes(b"paired latent data")
    result = subprocess.run([sys.executable, str(TOOL), "verify-latent", "--path", str(latent)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "non-empty paired latent" in result.stdout.lower()


@pytest.mark.parametrize("state", ("absent", "empty"))
def test_verify_latent_rejects_absent_or_empty_file(tmp_path, state):
    latent = tmp_path / "segment-1.latent"
    if state == "empty":
        latent.touch()
    result = subprocess.run([sys.executable, str(TOOL), "verify-latent", "--path", str(latent)],
                            capture_output=True, text=True, check=False)
    assert result.returncode != 0
    assert "latent" in result.stderr.lower()


def _media_report(*, frames, fps="24/1", duration=None, audio=True, video=True):
    duration = frames / 24 if duration is None else duration
    streams = []
    if video:
        streams.append({"index": 0, "codec_type": "video", "r_frame_rate": fps,
                        "avg_frame_rate": fps, "nb_read_frames": str(frames),
                        "duration": str(duration)})
    if audio:
        streams.append({"index": len(streams), "codec_type": "audio", "duration": str(duration)})
    return {"streams": streams, "format": {"duration": str(duration)}}


class FakeMediaTools:
    def __init__(self, paths, *, first=None, second=None, output=None, decode_status=0):
        self.paths = paths
        self.reports = {
            str(paths["first"].resolve()): first or _media_report(frames=73),
            str(paths["second"].resolve()): second or _media_report(frames=51),
        }
        self.output_report = output or _media_report(frames=124)
        self.decode_status = decode_status
        self.calls = []

    def __call__(self, args, **kwargs):
        assert isinstance(args, (list, tuple))
        assert kwargs.get("shell") is not True
        args = list(args)
        self.calls.append(args)
        command = Path(args[0]).name.lower()
        if command == "ffprobe":
            media_path = str(Path(args[-1]).resolve())
            assert "-count_frames" in args
            payload = self.output_report if media_path == str(self.paths["output"].resolve()) \
                else self.reports[media_path]
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(payload), stderr="")
        if command != "ffmpeg":
            raise AssertionError(f"unexpected subprocess: {args}")
        if "-filter_complex" in args:
            self.paths["output"].write_bytes(b"joined video and audio")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(args, self.decode_status, stdout="",
                                           stderr="decode failed" if self.decode_status else "")


def _join_case(tmp_path, monkeypatch, *, first=None, second=None, output=None, decode_status=0):
    paths = {name: tmp_path / f"{name}.mp4" for name in ("first", "second", "output")}
    paths["latent"] = tmp_path / "paired.latent"
    paths["first"].write_bytes(b"first segment")
    paths["second"].write_bytes(b"second segment")
    paths["latent"].write_bytes(b"paired latent")
    tool = _load_tool()
    fake = FakeMediaTools(paths, first=first, second=second, output=output,
                          decode_status=decode_status)
    monkeypatch.setattr(tool.subprocess, "run", fake)
    argv = ["join-verify", "--first", str(paths["first"]), "--second", str(paths["second"]),
            "--latent", str(paths["latent"]), "--output", str(paths["output"])]
    return tool, fake, paths, argv


def _bash_executable():
    if os.name == "nt":
        git = shutil.which("git")
        if git:
            candidate = Path(git).resolve().parents[1] / "bin" / "bash.exe"
            if candidate.is_file():
                return str(candidate)
    bash = shutil.which("bash")
    if not bash:
        raise RuntimeError("Bash is required to validate H3 setup integration")
    return bash


def _bash_path(path, bash):
    if os.name != "nt":
        return str(path)
    result = subprocess.run([bash, "-c", 'cygpath -u "$1"', "path", str(path)],
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


def test_join_verifies_inputs_output_and_full_decode_without_shell(tmp_path, monkeypatch):
    tool, fake, paths, argv = _join_case(tmp_path, monkeypatch)
    assert tool.main(argv) == 0
    assert paths["output"].read_bytes() == b"joined video and audio"
    ffmpeg_calls = [call for call in fake.calls if Path(call[0]).name.lower() == "ffmpeg"]
    assert len(ffmpeg_calls) == 2
    join_call = next(call for call in ffmpeg_calls if "-filter_complex" in call)
    assert "-n" in join_call
    assert "concat=n=2:v=1:a=1" in join_call[join_call.index("-filter_complex") + 1]
    assert str(paths["first"]) in join_call
    assert str(paths["second"]) in join_call
    assert any("-f" in call and "null" in call for call in ffmpeg_calls)


@pytest.mark.parametrize(
    ("which", "report", "message"),
    (("first", _media_report(frames=72), "73"),
     ("second", _media_report(frames=50), "51"),
     ("first", _media_report(frames=73, fps="25/1"), "24"),
     ("second", _media_report(frames=51, duration=2.5), "duration")),
)
def test_join_rejects_invalid_input_frames_fps_or_duration(tmp_path, monkeypatch, capsys,
                                                           which, report, message):
    kwargs = {which: report}
    tool, fake, paths, argv = _join_case(tmp_path, monkeypatch, **kwargs)
    assert tool.main(argv) != 0
    assert message.lower() in capsys.readouterr().err.lower()
    assert not any("-filter_complex" in call for call in fake.calls)


@pytest.mark.parametrize(
    ("report", "message"),
    ((_media_report(frames=123), "124"),
     (_media_report(frames=124, fps="30/1"), "24"),
     (_media_report(frames=124, duration=5.5), "duration"),
     (_media_report(frames=124, audio=False), "audio"),
     (_media_report(frames=124, video=False), "video")),
)
def test_join_rejects_invalid_output_metadata(tmp_path, monkeypatch, capsys, report, message):
    tool, _fake, _paths, argv = _join_case(tmp_path, monkeypatch, output=report)
    assert tool.main(argv) != 0
    assert message.lower() in capsys.readouterr().err.lower()


def test_join_rejects_output_audio_with_wrong_duration(tmp_path, monkeypatch, capsys):
    report = _media_report(frames=124)
    report["streams"][1]["duration"] = "4.0"
    tool, _fake, _paths, argv = _join_case(tmp_path, monkeypatch, output=report)
    assert tool.main(argv) != 0
    assert "duration" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("missing_stream", ("audio", "video"))
def test_join_rejects_inputs_without_audio_or_video(tmp_path, monkeypatch, capsys, missing_stream):
    report = _media_report(frames=73 if missing_stream == "audio" else 73,
                           audio=missing_stream != "audio", video=missing_stream != "video")
    tool, _fake, _paths, argv = _join_case(tmp_path, monkeypatch, first=report)
    assert tool.main(argv) != 0
    assert missing_stream in capsys.readouterr().err.lower()


def test_join_rejects_full_decode_failure(tmp_path, monkeypatch, capsys):
    tool, _fake, _paths, argv = _join_case(tmp_path, monkeypatch, decode_status=1)
    assert tool.main(argv) != 0
    assert "decode" in capsys.readouterr().err.lower()


def test_join_refuses_to_overwrite_existing_output(tmp_path, monkeypatch, capsys):
    tool, fake, paths, argv = _join_case(tmp_path, monkeypatch)
    paths["output"].write_bytes(b"keep me")
    assert tool.main(argv) != 0
    assert paths["output"].read_bytes() == b"keep me"
    assert fake.calls == []
    assert "exist" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("missing", ("first", "second", "latent"))
def test_join_rejects_absent_input_or_latent_before_running_tools(tmp_path, monkeypatch, capsys, missing):
    tool, fake, paths, argv = _join_case(tmp_path, monkeypatch)
    paths[missing].unlink()
    assert tool.main(argv) != 0
    assert fake.calls == []
    error = capsys.readouterr().err.lower()
    assert missing in error or "missing" in error


def test_join_rejects_empty_latent_before_running_tools(tmp_path, monkeypatch, capsys):
    tool, fake, paths, argv = _join_case(tmp_path, monkeypatch)
    paths["latent"].write_bytes(b"")
    assert tool.main(argv) != 0
    assert fake.calls == []
    assert "empty" in capsys.readouterr().err.lower()


def test_setup_runs_experimental_preflight_only_after_comfyui_restart():
    text = (ROOT / "scripts" / "setupp_h3_studio.sh").read_text(encoding="utf-8")
    main = text.split("main_studio() {", 1)[1].split("\n}\n", 1)[0]
    assert "h3_studio_verify_motion_context_smoke" in main
    assert main.index("h3_profile_finish") < main.index("h3_studio_verify_motion_context_smoke")
    assert main.index("h3_studio_install_motion_context_smoke_tool") < main.index("h3_profile_finish")
    assert "preflight --comfy-url" in text
    assert "MOTION_CONTEXT_SMOKE_TOOL_URL" in text
    assert "continuing T8 setup" in text


@pytest.mark.parametrize("download_fails", (False, True))
def test_setup_stages_preflight_tool_when_bootstrap_has_no_sibling_script(
    tmp_path, download_fails
):
    text = (ROOT / "scripts" / "setupp_h3_studio.sh").read_text(encoding="utf-8")
    assert f'MOTION_CONTEXT_SMOKE_TOOL_REV="${{MOTION_CONTEXT_SMOKE_TOOL_REV:-{SMOKE_TOOL_REV}}}"' in text
    assert 'MOTION_CONTEXT_SMOKE_TOOL_URL="${MOTION_CONTEXT_SMOKE_TOOL_URL:-https://raw.githubusercontent.com/halsn/vast_setup_script/$MOTION_CONTEXT_SMOKE_TOOL_REV/scripts/h3_motion_context_smoke.py}"' in text
    install_name = "h3_studio_install_motion_context_smoke_tool() {"
    verify_name = "h3_studio_verify_motion_context_smoke() {"
    assert install_name in text
    assert verify_name in text
    install = install_name + text.split(install_name, 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    verify = verify_name + text.split(verify_name, 1)[1].split("\n}\n", 1)[0] + "\n}\n"
    bash = _bash_executable()
    script_dir = tmp_path / "standalone-bootstrap"
    tool_dir = tmp_path / "installed-tools"
    script_dir.mkdir()
    installed_tool = tool_dir / "h3_motion_context_smoke.py"
    expected_url = f"https://raw.githubusercontent.com/halsn/vast_setup_script/{SMOKE_TOOL_REV}/scripts/h3_motion_context_smoke.py"
    script_dir_arg = shlex.quote(_bash_path(script_dir, bash))
    tool_dir_arg = shlex.quote(_bash_path(tool_dir, bash))
    installed_tool_arg = shlex.quote(_bash_path(installed_tool, bash))
    tool_arg = shlex.quote(_bash_path(TOOL, bash))
    python_arg = shlex.quote(_bash_path(Path(sys.executable), bash))
    harness = f"""set -Eeuo pipefail
SCRIPT_DIR={script_dir_arg}
H3_T8_SMOKE_TOOL_DIR={tool_dir_arg}
MOTION_CONTEXT_SMOKE_TOOL_URL={shlex.quote(expected_url)}
EXPECTED_URL={shlex.quote(expected_url)}
INSTALLED_TOOL={installed_tool_arg}
REAL_TOOL={tool_arg}
REAL_PYTHON={python_arg}
COMFY_PYTHON=fake_python
COMFY_PORT=18188
FAIL_CURL={'1' if download_fails else '0'}
FETCHED_URL=
h3_profile_warn() {{ printf '[WARN] %s\\n' "$*" >&2; }}
h3_profile_info() {{ printf '[INFO] %s\\n' "$*" >&2; }}
curl() {{
  local url="" destination=""
  while (($#)); do
    if [[ "$1" == http* ]]; then url="$1"; fi
    if [[ "$1" == -o ]]; then shift; destination="$1"; fi
    shift
  done
  [[ "$url" == "$EXPECTED_URL" ]] || return 90
  [[ "$FAIL_CURL" == 0 ]] || return 22
  cp "$REAL_TOOL" "$destination"
  FETCHED_URL="$url"
}}
fake_python() {{
  if [[ "$2" == --help ]]; then
    "$REAL_PYTHON" "$1" --help >/dev/null
  elif [[ "$2" == preflight ]]; then
    [[ "$1" == "$INSTALLED_TOOL" && -f "$1" ]]
    [[ "$3" == --comfy-url && "$4" == http://127.0.0.1:18188 ]]
    printf 'PREFLIGHT_OK\\n'
  else
    return 91
  fi
}}
{install}
{verify}
h3_studio_install_motion_context_smoke_tool
h3_studio_verify_motion_context_smoke
if [[ "$FAIL_CURL" == 0 ]]; then
  [[ "$FETCHED_URL" == "$EXPECTED_URL" && -f "$INSTALLED_TOOL" ]]
  printf 'FETCH_AND_PREFLIGHT_OK\\n'
else
  [[ ! -e "$INSTALLED_TOOL" ]]
  printf 'PREFLIGHT_UNAVAILABLE_BUT_CONTINUING\\n'
fi
printf 'T8_READY\\n'
"""
    env = os.environ.copy()
    git_root = Path(bash).resolve().parents[1]
    env["PATH"] = os.pathsep.join((str(git_root / "usr" / "bin"),
                                   str(git_root / "bin"), env.get("PATH", "")))
    result = subprocess.run([bash, "-c", harness], capture_output=True,
                            text=True, cwd=ROOT, env=env, check=False)
    assert result.returncode == 0, result.stderr
    assert "T8_READY" in result.stdout
    if download_fails:
        assert "PREFLIGHT_UNAVAILABLE_BUT_CONTINUING" in result.stdout
        assert "download failed" in result.stderr
    else:
        assert "FETCH_AND_PREFLIGHT_OK" in result.stdout
        assert "PREFLIGHT_OK" in result.stdout

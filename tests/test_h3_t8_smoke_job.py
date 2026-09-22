from pathlib import Path
import importlib.util
import json
import os
import time


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "h3_t8_smoke_job.py"


def load_module():
    spec = importlib.util.spec_from_file_location("h3_t8_smoke_job_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def wait_terminal(module, root, job_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = module.status(root, job_id)
        if value["state"] in {"completed", "failed", "lost", "invalid"}:
            return value
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def paid_summary(chain_id: str) -> dict:
    return {
        "status": "passed",
        "chain_id": chain_id,
        "first_prompt_id": "prompt-first",
        "resume_prompt_id": "prompt-resume",
        "contract_sha256": "a" * 64,
        "manifest_revision": 2,
        "accepted_segments": 2,
        "first_segment_unchanged_after_resume": True,
        "prompt_relay_applied_all_segments": True,
        "eav_verified_all_segments": True,
        "media": {
            "frames": 192,
            "fps": 24,
            "width": 512,
            "height": 288,
            "audio_streams": 1,
            "duration_seconds": 8.0,
            "bytes": 4096,
            "sha256": "b" * 64,
            "comfyui_view": {
                "filename": "final.mp4",
                "subfolder": "minimax_h3_t8_long_video/chain/assembled",
                "type": "output",
            },
        },
    }


def write_smoke(path: Path, *, marker: Path, exit_code: int = 0):
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import pathlib\n"
        f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
        "print('[GPU 1/6] synthetic smoke', flush=True)\n"
        f"raise SystemExit({exit_code})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_start_is_idempotent_for_same_job_id(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    marker = tmp_path / "marker"
    smoke = tmp_path / "smoke.py"
    write_smoke(smoke, marker=marker)

    job_id = "a" * 32
    first = module.start(root, job_id, False, str(smoke))
    second = module.start(root, job_id, False, str(smoke))
    assert first["job_id"] == second["job_id"] == job_id

    terminal = wait_terminal(module, root, job_id)
    assert terminal["state"] == "completed"
    assert terminal["returncode"] == 0
    assert marker.read_text(encoding="utf-8") == "ran"


def test_existing_job_rejects_mode_change(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    marker = tmp_path / "marker"
    smoke = tmp_path / "smoke.py"
    write_smoke(smoke, marker=marker)

    job_id = "b" * 32
    module.start(root, job_id, False, str(smoke))
    try:
        module.start(root, job_id, True, str(smoke))
    except RuntimeError as exc:
        assert "different execution mode" in str(exc)
    else:
        raise AssertionError("same job id must not change execution mode")


def test_existing_job_rejects_request_identity_mismatch(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    job_id = "9" * 32
    target = root / job_id
    target.mkdir(parents=True)
    module.atomic_json(
        target / "request.json",
        {
            "schema": module.SCHEMA,
            "job_id": "8" * 32,
            "execute": False,
            "chain_id": "wb_t8_" + job_id[:20],
            "evidence_dir": str(target / "evidence"),
        },
    )

    try:
        module.status(root, job_id)
    except RuntimeError as exc:
        assert "identity mismatch" in str(exc)
    else:
        raise AssertionError("mismatched durable job identity must be rejected")


def test_job_request_rejects_evidence_path_outside_job_root(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    job_id = "7" * 32
    target = root / job_id
    target.mkdir(parents=True)
    module.atomic_json(
        target / "request.json",
        {
            "schema": module.SCHEMA,
            "job_id": job_id,
            "execute": True,
            "chain_id": "wb_t8_" + job_id[:20],
            "evidence_dir": str(tmp_path / "outside-evidence"),
        },
    )

    try:
        module.status(root, job_id)
    except RuntimeError as exc:
        assert "escaped job root" in str(exc)
    else:
        raise AssertionError("evidence path outside the durable job must be rejected")


def test_existing_result_rejects_wrong_job_identity(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    job_id = "6" * 32
    target = root / job_id
    target.mkdir(parents=True)
    module.atomic_json(
        target / "request.json",
        {
            "schema": module.SCHEMA,
            "job_id": job_id,
            "execute": True,
            "chain_id": "wb_t8_" + job_id[:20],
            "evidence_dir": str(target / "evidence"),
        },
    )
    module.atomic_json(
        target / "result.json",
        {
            "schema": module.SCHEMA,
            "job_id": "5" * 32,
            "state": "completed",
            "returncode": 0,
            "message": "wrong job",
            "summary": paid_summary("wb_t8_" + job_id[:20]),
        },
    )

    value = module.status(root, job_id)
    assert value["state"] == "failed"
    assert value["returncode"] == 74
    assert "result identity mismatch" in value["message"]


def test_existing_completed_result_is_revalidated_before_reporting_success(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    job_id = "5" * 32
    target = root / job_id
    target.mkdir(parents=True)
    module.atomic_json(
        target / "request.json",
        {
            "schema": module.SCHEMA,
            "job_id": job_id,
            "execute": True,
            "chain_id": "wb_t8_" + job_id[:20],
            "evidence_dir": str(target / "evidence"),
        },
    )
    bad = paid_summary("wb_t8_" + job_id[:20])
    bad["eav_verified_all_segments"] = False
    module.atomic_json(
        target / "result.json",
        {
            "schema": module.SCHEMA,
            "job_id": job_id,
            "state": "completed",
            "returncode": 0,
            "message": "old helper claimed success",
            "summary": bad,
        },
    )

    value = module.status(root, job_id)
    assert value["state"] == "failed"
    assert value["returncode"] == 74
    assert "eav_verified_all_segments" in value["message"]


def test_tail_lines_is_bounded_without_loading_semantic_state(tmp_path):
    module = load_module()
    path = tmp_path / "job.log"
    path.write_text(
        "".join(f"line-{index}\n" for index in range(1000)),
        encoding="utf-8",
    )

    assert module.tail_lines(path, 3) == ["line-997", "line-998", "line-999"]
    assert module.tail_lines(path, 0) == []


def test_dead_runner_without_result_is_lost(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    job_id = "c" * 32
    target = root / job_id
    target.mkdir(parents=True)
    module.atomic_json(
        target / "request.json",
        {
            "schema": module.SCHEMA,
            "job_id": job_id,
            "execute": True,
            "chain_id": "wb_t8_" + job_id[:20],
            "evidence_dir": str(target / "evidence"),
        },
    )
    (target / "pid").write_text("99999999\n", encoding="utf-8")

    value = module.status(root, job_id)
    assert value["state"] == "lost"
    assert "without an atomic result" in value["message"]


def test_status_keeps_tracking_detached_smoke_child(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    job_id = "e" * 32
    target = root / job_id
    target.mkdir(parents=True)
    module.atomic_json(
        target / "request.json",
        {
            "schema": module.SCHEMA,
            "job_id": job_id,
            "execute": True,
            "chain_id": "wb_t8_" + job_id[:20],
            "evidence_dir": str(target / "evidence"),
        },
    )
    (target / "pid").write_text("99999999\n", encoding="utf-8")
    (target / "child_pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")

    value = module.status(root, job_id)
    assert value["state"] == "running"
    assert value["child_pid"] == os.getpid()
    assert "detached smoke child" in value["message"]


def test_status_recovers_passed_job_from_smoke_evidence(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    job_id = "f" * 32
    target = root / job_id
    evidence = target / "evidence"
    evidence.mkdir(parents=True)
    module.atomic_json(
        target / "request.json",
        {
            "schema": module.SCHEMA,
            "job_id": job_id,
            "execute": True,
            "chain_id": "wb_t8_" + job_id[:20],
            "evidence_dir": str(evidence),
        },
    )
    (target / "pid").write_text("99999998\n", encoding="utf-8")
    (target / "child_pid").write_text("99999999\n", encoding="utf-8")
    module.atomic_json(
        evidence / "result.json",
        paid_summary("wb_t8_" + job_id[:20]),
    )

    value = module.status(root, job_id)
    assert value["state"] == "completed"
    assert value["returncode"] == 0
    assert value["summary"]["accepted_segments"] == 2
    assert "recovered from completed smoke evidence" in value["message"]


def test_paid_lock_survives_controller_loss_via_detached_child(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    marker = tmp_path / "child-started"
    smoke = tmp_path / "slow-smoke.py"
    smoke.write_text(
        "#!/usr/bin/env python3\n"
        "import argparse, json, pathlib, time\n"
        "p = argparse.ArgumentParser()\n"
        "p.add_argument('--execute', action='store_true')\n"
        "p.add_argument('--chain-id')\n"
        "p.add_argument('--evidence-dir')\n"
        "a = p.parse_args()\n"
        f"pathlib.Path({str(marker)!r}).write_text('started')\n"
        "time.sleep(0.8)\n"
        "e = pathlib.Path(a.evidence_dir)\n"
        "e.mkdir(parents=True, exist_ok=True)\n"
        "summary = {'status':'passed','chain_id':a.chain_id,"
        "'first_prompt_id':'prompt-first','resume_prompt_id':'prompt-resume',"
        "'contract_sha256':'a'*64,'manifest_revision':2,'accepted_segments':2,"
        "'first_segment_unchanged_after_resume':True,"
        "'prompt_relay_applied_all_segments':True,'eav_verified_all_segments':True,"
        "'media':{'frames':192,'fps':24,'width':512,'height':288,"
        "'audio_streams':1,'duration_seconds':8.0,'bytes':4096,'sha256':'b'*64,"
        "'comfyui_view':{'filename':'final.mp4','subfolder':'minimax_h3_t8_long_video/chain/assembled','type':'output'}}}\n"
        "(e / 'result.json').write_text(json.dumps(summary))\n",
        encoding="utf-8",
    )
    smoke.chmod(0o755)

    first_id = "1" * 32
    module.start(root, first_id, True, str(smoke))
    first_dir = root / first_id
    deadline = time.time() + 3
    while time.time() < deadline:
        if marker.is_file() and (first_dir / "child_pid").is_file():
            break
        time.sleep(0.02)
    else:
        raise AssertionError("first paid smoke child never started")

    controller_pid = int((first_dir / "pid").read_text(encoding="utf-8"))
    os.kill(controller_pid, 9)
    deadline = time.time() + 2
    while time.time() < deadline:
        first_status = module.status(root, first_id)
        if first_status["state"] == "running" and first_status.get("child_pid"):
            break
        time.sleep(0.02)
    else:
        raise AssertionError("detached child was not tracked after controller loss")

    second_id = "2" * 32
    module.start(root, second_id, True, str(smoke))
    second = wait_terminal(module, root, second_id)
    assert second["state"] == "failed"
    assert second["returncode"] == 75
    assert "another paid T8 validation is running" in second["message"]

    first = wait_terminal(module, root, first_id, timeout=4)
    assert first["state"] == "completed"
    assert first["summary"]["status"] == "passed"
    assert "recovered from completed smoke evidence" in first["message"]


def test_paid_review_receipt_rejects_path_traversal():
    module = load_module()
    chain_id = "wb_t8_" + ("4" * 20)
    summary = paid_summary(chain_id)
    summary["media"]["comfyui_view"]["subfolder"] = "../outside"
    try:
        module.validate_paid_summary(summary, chain_id)
    except RuntimeError as exc:
        assert "media.comfyui_view.subfolder" in str(exc)
    else:
        raise AssertionError("review receipt path traversal must fail closed")


def test_paid_partial_evidence_fails_closed(tmp_path):
    module = load_module()
    chain_id = "wb_t8_" + ("4" * 20)
    summary = paid_summary(chain_id)
    summary["prompt_relay_applied_all_segments"] = False
    try:
        module.validate_paid_summary(summary, chain_id)
    except RuntimeError as exc:
        assert "prompt_relay_applied_all_segments" in str(exc)
    else:
        raise AssertionError("partial paid evidence must fail closed")


def test_paid_zero_exit_without_evidence_fails_closed(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    marker = tmp_path / "marker"
    smoke = tmp_path / "smoke.py"
    write_smoke(smoke, marker=marker)

    job_id = "3" * 32
    module.start(root, job_id, True, str(smoke))
    terminal = wait_terminal(module, root, job_id)
    assert terminal["state"] == "failed"
    assert terminal["returncode"] == 74
    assert "without atomic evidence" in terminal["message"]


def test_paid_job_uses_fixed_chain_and_evidence_arguments(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    argv_file = tmp_path / "argv.json"
    smoke = tmp_path / "smoke.py"
    smoke.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(argv_file)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "args = sys.argv[1:]\n"
        "chain = args[args.index('--chain-id') + 1]\n"
        "e = pathlib.Path(args[args.index('--evidence-dir') + 1])\n"
        "e.mkdir(parents=True, exist_ok=True)\n"
        "summary = {'status':'passed','chain_id':chain,"
        "'first_prompt_id':'prompt-first','resume_prompt_id':'prompt-resume',"
        "'contract_sha256':'a'*64,'manifest_revision':2,'accepted_segments':2,"
        "'first_segment_unchanged_after_resume':True,"
        "'prompt_relay_applied_all_segments':True,'eav_verified_all_segments':True,"
        "'media':{'frames':192,'fps':24,'width':512,'height':288,"
        "'audio_streams':1,'duration_seconds':8.0,'bytes':4096,'sha256':'b'*64,"
        "'comfyui_view':{'filename':'final.mp4','subfolder':'minimax_h3_t8_long_video/chain/assembled','type':'output'}}}\n"
        "(e / 'result.json').write_text(json.dumps(summary))\n",
        encoding="utf-8",
    )
    smoke.chmod(0o755)

    job_id = "d" * 32
    module.start(root, job_id, True, str(smoke))
    terminal = wait_terminal(module, root, job_id)
    assert terminal["state"] == "completed"
    argv = json.loads(argv_file.read_text(encoding="utf-8"))
    assert argv[:3] == ["--execute", "--chain-id", "wb_t8_" + job_id[:20]]
    assert argv[3] == "--evidence-dir"
    assert Path(argv[4]) == (root / job_id / "evidence").resolve()

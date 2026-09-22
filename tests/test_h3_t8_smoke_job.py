from pathlib import Path
import importlib.util
import json
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


def test_paid_job_uses_fixed_chain_and_evidence_arguments(tmp_path):
    module = load_module()
    root = tmp_path / "jobs"
    argv_file = tmp_path / "argv.json"
    smoke = tmp_path / "smoke.py"
    smoke.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        f"pathlib.Path({str(argv_file)!r}).write_text(json.dumps(sys.argv[1:]))\n",
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

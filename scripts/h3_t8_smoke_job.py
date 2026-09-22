#!/usr/bin/env python3
"""Durable, idempotent launcher for the fixed H3 T8 release smoke."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SCHEMA = "h3.t8.smoke_job.v1"
DEFAULT_ROOT = Path("/tmp/h3-t8-smoke-jobs")
DEFAULT_SMOKE = "/usr/local/bin/h3-t8-long-video-smoke"
JOB_RE = re.compile(r"^[0-9a-f]{32}$")


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def job_dir(root: Path, job_id: str) -> Path:
    if not JOB_RE.fullmatch(job_id):
        raise ValueError("job id must be 32 lowercase hex characters")
    root = root.resolve()
    path = (root / job_id).resolve()
    if root not in path.parents:
        raise ValueError("job path escaped root")
    return path


def alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    # A detached child that exited before its parent observed it can remain as
    # a zombie briefly. kill(pid, 0) still succeeds for zombies, so inspect
    # procfs first on Linux and treat Z as terminal.
    stat = Path(f"/proc/{pid}/stat")
    if stat.is_file():
        try:
            fields = stat.read_text(encoding="utf-8", errors="replace").split()
            if len(fields) >= 3 and fields[2] == "Z":
                return False
        except OSError:
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def tail_lines(path: Path, count: int) -> list[str]:
    if not path.is_file() or count <= 0:
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]


def status(root: Path, job_id: str, tail: int = 80) -> dict:
    target = job_dir(root, job_id)
    if not target.is_dir():
        return {"schema": SCHEMA, "job_id": job_id, "state": "missing", "log_tail": []}
    request_path = target / "request.json"
    if not request_path.is_file():
        return {
            "schema": SCHEMA,
            "job_id": job_id,
            "state": "invalid",
            "message": "job directory exists without request.json",
            "log_tail": tail_lines(target / "job.log", tail),
        }
    request = read_json(request_path)
    result_path = target / "result.json"

    def read_pid(name: str) -> int | None:
        path = target / name
        if not path.is_file():
            return None
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    pid = read_pid("pid")
    child_pid = read_pid("child_pid")
    result = {
        "schema": SCHEMA,
        "job_id": job_id,
        "execute": bool(request.get("execute")),
        "chain_id": request.get("chain_id"),
        "evidence_dir": request.get("evidence_dir"),
        "pid": pid,
        "child_pid": child_pid,
        "log_tail": tail_lines(target / "job.log", tail),
    }
    if result_path.is_file():
        final = read_json(result_path)
        result.update(final)
        return result

    # If the controller died after launching the paid smoke, the smoke process
    # remains detached and inherits the paid-job lock fd. Keep attaching to that
    # child instead of allowing the UI to start a replacement job.
    if alive(child_pid):
        result["state"] = "running"
        if not alive(pid):
            result["message"] = "controller exited; detached smoke child is still running"
        return result

    # The smoke harness writes its own atomic evidence result before returning.
    # This lets status recover a completed paid run even if the tiny controller
    # process died in the narrow window before it could publish job/result.json.
    evidence_dir = request.get("evidence_dir")
    if bool(request.get("execute")) and isinstance(evidence_dir, str):
        evidence_result = Path(evidence_dir) / "result.json"
        if evidence_result.is_file():
            summary = read_json(evidence_result)
            if summary.get("status") == "passed":
                result.update(
                    {
                        "state": "completed",
                        "returncode": 0,
                        "message": "T8 validation recovered from completed smoke evidence",
                        "summary": summary,
                    }
                )
                return result

    result["state"] = "running" if alive(pid) else ("starting" if pid is None else "lost")
    if result["state"] == "lost":
        result["message"] = "runner exited without an atomic result or completed evidence"
    return result


def write_result(target: Path, state: str, code: int, message: str, summary=None) -> None:
    atomic_json(
        target / "result.json",
        {
            "state": state,
            "returncode": int(code),
            "message": message,
            "finished_at": time.time(),
            "summary": summary,
        },
    )


def run_job(root: Path, job_id: str) -> int:
    target = job_dir(root, job_id)
    request = read_json(target / "request.json")
    execute = bool(request["execute"])
    smoke = str(request["smoke_bin"])
    (target / "pid").write_text(str(os.getpid()) + "\n", encoding="utf-8")
    lock = None
    try:
        with (target / "job.log").open("a", encoding="utf-8", buffering=1) as log:
            print(f"[JOB] start {job_id} execute={execute}", file=log, flush=True)
            if execute:
                lock_path = root.resolve() / "paid.lock"
                lock_path.parent.mkdir(parents=True, exist_ok=True)
                lock = lock_path.open("a+")
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    write_result(target, "failed", 75, "another paid T8 validation is running")
                    return 75
            command = [smoke]
            if execute:
                command += [
                    "--execute",
                    "--chain-id",
                    str(request["chain_id"]),
                    "--evidence-dir",
                    str(request["evidence_dir"]),
                ]
            inherited_fds = (lock.fileno(),) if lock is not None else ()
            child = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
                pass_fds=inherited_fds,
            )
            (target / "child_pid").write_text(
                str(child.pid) + "\n",
                encoding="utf-8",
            )
            returncode = int(child.wait())
            summary = None
            evidence = Path(str(request["evidence_dir"])) / "result.json"
            if execute and returncode == 0:
                if not evidence.is_file():
                    returncode = 74
                    message = "paid T8 smoke exited zero without atomic evidence result"
                    write_result(target, "failed", returncode, message)
                    print(f"[JOB] {message}", file=log, flush=True)
                    return returncode
                summary = read_json(evidence)
                if summary.get("status") != "passed":
                    returncode = 74
                    message = "paid T8 smoke evidence did not report status=passed"
                    write_result(target, "failed", returncode, message, summary)
                    print(f"[JOB] {message}", file=log, flush=True)
                    return returncode
            state = "completed" if returncode == 0 else "failed"
            message = (
                "T8 validation completed"
                if state == "completed"
                else f"T8 validation exited with code {returncode}"
            )
            write_result(target, state, returncode, message, summary)
            print(f"[JOB] {message}", file=log, flush=True)
            return returncode
    except BaseException as exc:
        write_result(target, "failed", 70, f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if lock is not None:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            finally:
                lock.close()


def start(root: Path, job_id: str, execute: bool, smoke_bin: str) -> dict:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = job_dir(root, job_id)
    if target.exists():
        request = read_json(target / "request.json")
        if bool(request.get("execute")) is not bool(execute):
            raise RuntimeError("existing job id uses a different execution mode")
        return status(root, job_id)

    target.mkdir(mode=0o700)
    atomic_json(
        target / "request.json",
        {
            "schema": SCHEMA,
            "job_id": job_id,
            "execute": bool(execute),
            "smoke_bin": smoke_bin,
            "chain_id": "wb_t8_" + job_id[:20],
            "evidence_dir": str((target / "evidence").resolve()),
            "created_at": time.time(),
        },
    )
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--job-root",
            str(root),
            "_run",
            "--job-id",
            job_id,
        ],
        cwd="/",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    (target / "pid").write_text(str(process.pid) + "\n", encoding="utf-8")
    return status(root, job_id)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-root", default=str(DEFAULT_ROOT))
    parser.add_argument("--smoke-bin", default=os.environ.get("H3_T8_SMOKE_BIN", DEFAULT_SMOKE))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("start")
    p.add_argument("--job-id", required=True)
    p.add_argument("--execute", action="store_true")
    p = sub.add_parser("status")
    p.add_argument("--job-id", required=True)
    p.add_argument("--tail-lines", type=int, default=80)
    p = sub.add_parser("_run")
    p.add_argument("--job-id", required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    root = Path(args.job_root)
    if args.command == "start":
        print(json.dumps(start(root, args.job_id, args.execute, args.smoke_bin), ensure_ascii=False))
        return 0
    if args.command == "status":
        if not 0 <= args.tail_lines <= 500:
            raise ValueError("--tail-lines must be between 0 and 500")
        print(json.dumps(status(root, args.job_id, args.tail_lines), ensure_ascii=False))
        return 0
    return run_job(root, args.job_id)


if __name__ == "__main__":
    raise SystemExit(main())

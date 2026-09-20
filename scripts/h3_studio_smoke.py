#!/usr/bin/env python3
"""Smoke-test a deployed H3 Open Studio instance.

Default mode is read-only and does not submit GPU work. Pass --generate to run a
small T2V job through the Studio API; that consumes GPU time and may incur Vast
rental cost.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

TIMELINE_SOURCE = "ComfyUI-MiniMaxH3-TimelineDirector"
TIMELINE_TEMPLATE = "h3_timeline_director"
TIMELINE_UNET = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
TIMELINE_CLIP = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
TIMELINE_STEPS = 20
REQUIRED_TIMELINE_NODES = (
    "MiniMaxH3TimelinePlanner",
    "MiniMaxH3FiniteSegmentSampler",
    "MiniMaxH3TimelineSelfLiftSampler",
)


@dataclass
class HttpResult:
    status: int
    body: bytes
    headers: Any

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def _url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def request(
    url: str,
    *,
    method: str = "GET",
    payload: Any | None = None,
    timeout: float = 20,
) -> HttpResult:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return HttpResult(
                status=response.status,
                body=response.read(),
                headers=response.headers,
            )
    except urllib.error.HTTPError as exc:
        body = exc.read()
        detail = body.decode("utf-8", "replace")[:1000]
        raise RuntimeError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{method} {url} failed: {exc}") from exc


def check_contract(studio_url: str, comfy_url: str) -> None:
    print("[1/5] H3 Studio HTTP")
    home = request(_url(studio_url, "/"))
    if home.status != 200:
        raise RuntimeError(f"Studio root returned HTTP {home.status}")

    print("[2/5] H3 Studio -> ComfyUI bridge")
    status = request(_url(studio_url, "/api/comfyui/status")).json()
    if status.get("up") is not True:
        raise RuntimeError(f"Studio cannot reach ComfyUI: {status}")

    print("[3/5] Timeline Director nodes")
    catalog = request(_url(comfy_url, "/object_info"), timeout=30).json()
    missing = [name for name in REQUIRED_TIMELINE_NODES if name not in catalog]
    if missing:
        raise RuntimeError(
            "Timeline Director nodes are missing: " + ", ".join(missing)
        )

    def combo_options(node_name: str, input_name: str) -> list[str]:
        try:
            value = catalog[node_name]["input"]["required"][input_name][0]
        except (KeyError, IndexError, TypeError):
            return []
        return value if isinstance(value, list) else []

    if TIMELINE_UNET not in combo_options("UNETLoader", "unet_name"):
        raise RuntimeError(
            f"Timeline Director UNET is not selectable: {TIMELINE_UNET}"
        )
    if TIMELINE_CLIP not in combo_options("CLIPLoader", "clip_name"):
        raise RuntimeError(
            f"Timeline Director CLIP is not selectable: {TIMELINE_CLIP}"
        )

    print("[4/5] Timeline Director template catalog")
    templates = request(_url(comfy_url, "/workflow_templates")).json()
    available = templates.get(TIMELINE_SOURCE) or []
    if TIMELINE_TEMPLATE not in available:
        raise RuntimeError(
            f"Template {TIMELINE_TEMPLATE!r} is not listed for {TIMELINE_SOURCE!r}"
        )

    print("[5/5] Timeline Director template payload")
    path = (
        "/api/workflow_templates/"
        + urllib.parse.quote(TIMELINE_SOURCE, safe="")
        + "/"
        + urllib.parse.quote(TIMELINE_TEMPLATE + ".json", safe="")
    )
    workflow = request(_url(comfy_url, path)).json()
    if not isinstance(workflow, dict) or not workflow.get("nodes"):
        raise RuntimeError("Timeline Director template JSON is missing workflow nodes")

    serialized = json.dumps(workflow, ensure_ascii=False)
    unsupported = (
        "minimax_h3_fused_refdelta_r1024_turbo8_mystic07_int8_convrot.safetensors",
        r"minimax_h3\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    )
    bad = [value for value in unsupported if value in serialized]
    if bad:
        raise RuntimeError(
            "Timeline Director template still references unsupported models: "
            + ", ".join(bad)
        )
    for expected in (TIMELINE_UNET, TIMELINE_CLIP):
        if expected not in serialized:
            raise RuntimeError(
                f"Timeline Director template does not use installed model: {expected}"
            )

    scheduler_steps = [
        (node.get("widgets_values_named") or {}).get("steps")
        for node in workflow.get("nodes", [])
        if node.get("type") == "BasicScheduler"
    ]
    if scheduler_steps != [TIMELINE_STEPS]:
        raise RuntimeError(
            f"Timeline Director scheduler mismatch: {scheduler_steps} != {[TIMELINE_STEPS]}"
        )

    print("[OK] H3 Studio contract smoke passed")


def create_workspace(studio_url: str, name: str) -> None:
    result = request(
        _url(studio_url, "/api/workspaces"),
        method="POST",
        payload={"name": name},
    ).json()
    if result.get("name") != name:
        raise RuntimeError(f"Unexpected workspace response: {result}")


def delete_workspace(studio_url: str, name: str) -> None:
    request(
        _url(studio_url, f"/api/workspaces/{urllib.parse.quote(name, safe='')}"),
        method="DELETE",
    )


def submit_t2v(
    studio_url: str,
    workspace: str,
    *,
    prompt: str,
    width: int,
    height: int,
    duration: int,
    steps: int,
    seed: int,
) -> str:
    payload = {
        "prompt": prompt,
        "params": {
            "task_type": "t2v",
            "model": "pruned",
            "steps": steps,
            "res_mode": "custom",
            "custom_w": width,
            "custom_h": height,
            "duration": duration,
            "seed": seed,
            "low_vram": True,
            "turbo_lora": False,
            "sage": False,
        },
    }
    result = request(
        _url(
            studio_url,
            f"/api/workspaces/{urllib.parse.quote(workspace, safe='')}/generate",
        ),
        method="POST",
        payload=payload,
        timeout=60,
    ).json()
    job_id = result.get("job_id")
    if not job_id:
        raise RuntimeError(f"Generation did not return job_id: {result}")
    print(
        "[GPU] submitted",
        job_id,
        f"{result.get('width')}x{result.get('height')}",
        f"{result.get('length')} frames",
    )
    return str(job_id)


def wait_for_job(
    studio_url: str,
    job_id: str,
    *,
    timeout: int,
) -> dict[str, Any]:
    url = _url(
        studio_url,
        f"/api/jobs/{urllib.parse.quote(job_id, safe='')}/events",
    )
    deadline = time.monotonic() + timeout
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=min(30, timeout)) as response:
            while time.monotonic() < deadline:
                raw = response.readline()
                if not raw:
                    raise RuntimeError("SSE stream closed before a terminal event")
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:].strip())
                kind = event.get("type")
                if kind == "progress":
                    value = event.get("value")
                    maximum = event.get("max")
                    if value is not None and maximum:
                        print(f"[GPU] progress {value}/{maximum}")
                elif kind == "status":
                    print(f"[GPU] {event.get('message') or event}")
                elif kind == "done":
                    return event
                elif kind == "error":
                    raise RuntimeError(
                        "Generation failed: "
                        + str(event.get("message") or event)
                    )
    except urllib.error.URLError as exc:
        raise RuntimeError(f"SSE request failed: {exc}") from exc
    raise TimeoutError(f"Generation did not finish within {timeout}s")


def verify_generated_media(studio_url: str, workspace: str, event: dict[str, Any]) -> None:
    video = event.get("video")
    if not isinstance(video, str) or not video:
        raise RuntimeError(f"Generation completed without a video file: {event}")
    url = _url(
        studio_url,
        "/api/workspaces/"
        + urllib.parse.quote(workspace, safe="")
        + "/media/"
        + urllib.parse.quote(video, safe=""),
    )
    media = request(url, timeout=30)
    if len(media.body) < 1024:
        raise RuntimeError(f"Generated video is unexpectedly small: {len(media.body)} bytes")
    print(f"[OK] generated video verified: {video} ({len(media.body)} bytes)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate H3 Open Studio + Timeline Director. "
            "Default mode is read-only; --generate submits real GPU work."
        )
    )
    parser.add_argument(
        "--studio-url",
        default="http://127.0.0.1:18080",
        help="H3 Studio base URL",
    )
    parser.add_argument(
        "--comfy-url",
        default="http://127.0.0.1:18188",
        help="ComfyUI base URL",
    )
    parser.add_argument(
        "--generate",
        action="store_true",
        help="submit a small real T2V generation (uses GPU time / may incur cost)",
    )
    parser.add_argument("--workspace", default="vast-smoke")
    parser.add_argument(
        "--prompt",
        default="A red cube slowly rotates on a clean studio table, static camera.",
    )
    parser.add_argument("--width", type=int, default=480)
    parser.add_argument("--height", type=int, default=288)
    parser.add_argument("--duration", type=int, default=2)
    parser.add_argument("--steps", type=int, default=4)
    parser.add_argument("--seed", type=int, default=123456)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument(
        "--keep-workspace",
        action="store_true",
        help="do not delete the smoke-test workspace after generation",
    )
    args = parser.parse_args(argv)

    for name in ("width", "height"):
        value = getattr(args, name)
        if value < 32 or value % 32 != 0:
            parser.error(f"--{name} must be a positive multiple of 32")
    if args.duration < 1:
        parser.error("--duration must be >= 1")
    if args.steps < 1:
        parser.error("--steps must be >= 1")
    if args.timeout < 30:
        parser.error("--timeout must be >= 30")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    check_contract(args.studio_url, args.comfy_url)

    if not args.generate:
        print("[INFO] GPU generation skipped. Re-run with --generate for the release smoke.")
        return 0

    print(
        "[WARN] --generate submits real H3 GPU work and may incur Vast rental cost.",
        file=sys.stderr,
    )
    create_workspace(args.studio_url, args.workspace)
    try:
        job_id = submit_t2v(
            args.studio_url,
            args.workspace,
            prompt=args.prompt,
            width=args.width,
            height=args.height,
            duration=args.duration,
            steps=args.steps,
            seed=args.seed,
        )
        event = wait_for_job(args.studio_url, job_id, timeout=args.timeout)
        verify_generated_media(args.studio_url, args.workspace, event)
        print("[OK] real H3 Studio generation smoke passed")
        return 0
    finally:
        if not args.keep_workspace:
            try:
                delete_workspace(args.studio_url, args.workspace)
                print(f"[INFO] deleted smoke workspace: {args.workspace}")
            except Exception as exc:
                print(f"[WARN] could not delete smoke workspace: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())

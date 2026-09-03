import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from aiohttp import ClientError, ClientSession, WSMsgType, web
from yarl import URL

from runtime.worker_readiness import ReadinessError, build_ready_payload
from runtime.workflow_builder import WorkflowBuildError, build_h3_prompt


ALLOWED_PATHS = frozenset(
    {
        "/healthz",
        "/bootstrap/status",
        "/bootstrap/log",
        "/ready",
        "/prompt",
        "/history",
        "/queue",
        "/interrupt",
        "/system_stats",
        "/view",
        "/upload/image",
        "/ws",
    }
)
DEFAULT_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
DEFAULT_BOOTSTRAP_LOG_LINES = 200
MAX_BOOTSTRAP_LOG_LINES = 500
MAX_BOOTSTRAP_LOG_BYTES = 512 * 1024
SAFE_REQUEST_HEADERS = frozenset(
    {
        "accept",
        "accept-language",
        "cache-control",
        "content-type",
        "if-modified-since",
        "if-none-match",
        "origin",
        "range",
        "referer",
        "user-agent",
    }
)
SAFE_RESPONSE_HEADERS = frozenset(
    {
        "accept-ranges",
        "cache-control",
        "content-encoding",
        "content-range",
        "content-type",
        "content-length",
        "etag",
        "expires",
        "last-modified",
    }
)
@dataclass(frozen=True)
class GatewayConfig:
    token: str
    upstream_url: URL
    max_upload_bytes: int
    listen: str = "0.0.0.0"
    port: int = 8190
    runtime_config_path: Path = Path("/run/h3/runtime.json")
    template_catalog_path: Path = Path("/opt/h3/config/templates.json")
    comfyui_dir: Path = Path("/opt/ComfyUI")
    bootstrap_status_path: Path = Path("/run/h3/bootstrap.json")
    bootstrap_log_path: Path = Path("/var/log/h3/bootstrap.log")


CONFIG_KEY = web.AppKey("gateway_config", GatewayConfig)
SESSION_KEY = web.AppKey("http_session", ClientSession)
JOB_MAPPINGS_KEY = web.AppKey("job_mappings", dict)


class GatewayError(Exception):
    def __init__(self, status: int, error: str):
        super().__init__(error)
        self.status = status
        self.error = error


def validate_upstream_url(value: str) -> URL:
    if not value or not value.strip():
        raise ValueError("upstream URL must not be empty")

    try:
        upstream = URL(value)
    except ValueError as exc:
        raise ValueError("upstream URL is invalid") from exc

    if upstream.scheme not in {"http", "https"} or not upstream.host:
        raise ValueError("upstream URL must use http or https with a host")
    if upstream.user or upstream.password or upstream.query_string or upstream.fragment:
        raise ValueError("upstream URL must not contain credentials, query, or fragment")
    return upstream


def authorize(request: web.Request, expected_token: str) -> None:
    request_id = request.get("request_id") or uuid.uuid4().hex
    authorization = request.headers.get("Authorization", "")
    scheme, separator, supplied_token = authorization.partition(" ")
    valid = (
        separator == ""
        or scheme.lower() != "bearer"
        or not supplied_token
        or not hmac.compare_digest(supplied_token, expected_token)
    )
    if valid:
        raise web.HTTPUnauthorized(
            text=_error_text("unauthorized", 401, request_id),
            content_type="application/json",
            headers={"WWW-Authenticate": "Bearer"},
        )


def create_app(
    *,
    token: str,
    upstream_url: str = "http://127.0.0.1:8188",
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
    runtime_config_path: Optional[str] = None,
    template_catalog_path: Optional[str] = None,
    comfyui_dir: Optional[str] = None,
    bootstrap_status_path: Optional[str] = None,
    bootstrap_log_path: Optional[str] = None,
) -> web.Application:
    if not token or not token.strip():
        raise ValueError("H3_WORKER_TOKEN must not be empty")
    if max_upload_bytes <= 0:
        raise ValueError("H3_GATEWAY_MAX_UPLOAD_BYTES must be greater than zero")

    config = GatewayConfig(
        token=token,
        upstream_url=validate_upstream_url(upstream_url),
        max_upload_bytes=max_upload_bytes,
        runtime_config_path=Path(runtime_config_path or os.getenv("H3_RUNTIME_CONFIG", "/run/h3/runtime.json")),
        template_catalog_path=Path(template_catalog_path or os.getenv("H3_TEMPLATE_CATALOG", "/opt/h3/config/templates.json")),
        comfyui_dir=Path(comfyui_dir or os.getenv("COMFYUI_DIR", "/opt/ComfyUI")),
        bootstrap_status_path=Path(bootstrap_status_path or os.getenv("H3_BOOTSTRAP_STATUS_FILE", "/run/h3/bootstrap.json")),
        bootstrap_log_path=Path(bootstrap_log_path or os.getenv("H3_BOOTSTRAP_LOG_FILE", "/var/log/h3/bootstrap.log")),
    )
    app = web.Application(middlewares=[_gateway_middleware], client_max_size=None)
    app[CONFIG_KEY] = config
    app[JOB_MAPPINGS_KEY] = {}
    app.cleanup_ctx.append(_client_session_context)
    app.router.add_route("*", "/{tail:.*}", _dispatch)
    return app


@web.middleware
async def _gateway_middleware(request: web.Request, handler):
    request["request_id"] = uuid.uuid4().hex
    config = request.app[CONFIG_KEY]
    try:
        authorize(request, config.token)
        response = await handler(request)
    except web.HTTPException as exc:
        error = "unauthorized" if exc.status == 401 else "gateway_http_error"
        response = _error_response(
            error,
            exc.status,
            request["request_id"],
            headers={"WWW-Authenticate": "Bearer"} if exc.status == 401 else None,
        )
    except GatewayError as exc:
        response = _error_response(exc.error, exc.status, request["request_id"])
    except Exception:
        response = _error_response("gateway_error", 500, request["request_id"])

    if not getattr(response, "prepared", False):
        response.headers["X-Request-ID"] = request["request_id"]
    return response


async def _dispatch(request: web.Request) -> web.StreamResponse:
    if not _is_allowed_path(request.path):
        return _error_response("not_found", 404, request["request_id"])
    if request.path == "/healthz":
        return web.json_response(
            {"status": "healthy", "request_id": request["request_id"]}
        )
    if request.path == "/bootstrap/status":
        return web.json_response(_load_bootstrap_status(request.app[CONFIG_KEY].bootstrap_status_path))
    if request.path == "/bootstrap/log":
        offset, limit, generation = _bootstrap_log_cursor(request)
        return web.json_response(_load_bootstrap_log(request.app[CONFIG_KEY].bootstrap_log_path, offset, limit, generation))
    if request.path == "/ready":
        try:
            payload = build_ready_payload(
                request.app[CONFIG_KEY].runtime_config_path,
                request.app[CONFIG_KEY].template_catalog_path,
                request.app[CONFIG_KEY].comfyui_dir,
            )
        except ReadinessError as exc:
            raise GatewayError(503, "worker_not_ready") from exc
        return web.json_response(payload)
    if request.path.startswith("/history/"):
        local_job_id = request.path.removeprefix("/history/")
        remote_job_id = request.app[JOB_MAPPINGS_KEY].get(local_job_id)
        if remote_job_id:
            return await _proxy_http(request, path=f"/history/{remote_job_id}")
    if request.path == "/ws":
        local_job_id = request.query.get("job_id")
        if local_job_id and local_job_id in request.app[JOB_MAPPINGS_KEY]:
            return await _proxy_worker_websocket(request, local_job_id)
        return await _proxy_websocket(request)
    if request.path == "/prompt":
        return await _handle_prompt(request)
    return await _proxy_http(request)


def _is_allowed_path(path: str) -> bool:
    if path in ALLOWED_PATHS:
        return True
    if path.startswith("/history/"):
        prompt_id = path.removeprefix("/history/")
        return bool(prompt_id) and "/" not in prompt_id
    return False


async def _handle_prompt(request: web.Request) -> web.StreamResponse:
    body = await _read_bounded_body(request, request.app[CONFIG_KEY].max_upload_bytes)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        payload = None

    if not _is_logical_submission(payload):
        return await _proxy_http(request, body=body)

    try:
        workflow = build_h3_prompt(
            payload["template_id"],
            payload["prompt"],
            payload.get("negative_prompt", ""),
            payload["parameters"],
            payload.get("assets", []),
            gpu_memory_mib=_runtime_memory_mib(request.app[CONFIG_KEY].runtime_config_path),
        )
    except (KeyError, TypeError, WorkflowBuildError) as exc:
        raise GatewayError(400, "invalid_submission") from exc

    native_body = json.dumps({"prompt": workflow}, separators=(",", ":")).encode("utf-8")
    response = await _post_native_prompt(request, native_body)
    if response.status >= 400:
        return response
    try:
        response_payload = json.loads(response.body.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError) as exc:
        raise GatewayError(502, "invalid_upstream_response") from exc
    remote_job_id = response_payload.get("prompt_id") if isinstance(response_payload, dict) else None
    node_errors = response_payload.get("node_errors") if isinstance(response_payload, dict) else None
    if not isinstance(remote_job_id, str) or not remote_job_id:
        if node_errors:
            raise GatewayError(422, "workflow_rejected")
        raise GatewayError(502, "invalid_upstream_response")
    local_job_id = payload["job_id"]
    request.app[JOB_MAPPINGS_KEY][local_job_id] = remote_job_id
    return web.json_response({"remote_job_id": remote_job_id, "prompt_id": remote_job_id})


def _is_logical_submission(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and isinstance(payload.get("job_id"), str)
        and isinstance(payload.get("template_id"), str)
        and isinstance(payload.get("prompt"), str)
        and isinstance(payload.get("parameters"), dict)
        and isinstance(payload.get("assets", []), list)
    )


def _runtime_memory_mib(path: Path) -> int | None:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        gpu = payload.get("gpu") if isinstance(payload, dict) else None
        value = gpu.get("total_memory_mib") if isinstance(gpu, dict) else None
        return value if isinstance(value, int) and value > 0 else None
    except (OSError, TypeError, ValueError):
        return None


async def _proxy_http(
    request: web.Request, *, body: bytes | None = None, path: str | None = None
) -> web.StreamResponse:
    if body is None:
        body = await _read_bounded_body(request, request.app[CONFIG_KEY].max_upload_bytes)
    body = _rewrite_interrupt_body(request, body)
    upstream_url = _build_upstream_url(request, path=path)
    headers = _selected_headers(request.headers, SAFE_REQUEST_HEADERS)
    session: ClientSession = request.app[SESSION_KEY]

    try:
        async with session.request(
            request.method,
            upstream_url,
            headers=headers,
            data=body,
            allow_redirects=False,
        ) as upstream_response:
            if upstream_response.status >= 400:
                return _error_response(
                    "upstream_error",
                    upstream_response.status,
                    request["request_id"],
                )
            response_headers = _selected_headers(
                upstream_response.headers, SAFE_RESPONSE_HEADERS
            )
            response = web.StreamResponse(
                status=upstream_response.status,
                headers=response_headers,
            )
            response.headers["X-Request-ID"] = request["request_id"]
            await response.prepare(request)
            async for chunk in upstream_response.content.iter_chunked(64 * 1024):
                await response.write(chunk)
            await response.write_eof()
            return response
    except (ClientError, asyncio.TimeoutError) as exc:
        raise GatewayError(502, "upstream_unavailable") from exc


async def _post_native_prompt(request: web.Request, body: bytes) -> web.Response:
    session: ClientSession = request.app[SESSION_KEY]
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    try:
        async with session.post(
            _build_upstream_url(request),
            headers=headers,
            data=body,
            allow_redirects=False,
        ) as upstream_response:
            response_body = await upstream_response.read()
            if upstream_response.status >= 400:
                return _error_response("upstream_error", upstream_response.status, request["request_id"])
            return web.Response(
                status=upstream_response.status,
                body=response_body,
                headers=_selected_headers(upstream_response.headers, SAFE_RESPONSE_HEADERS),
            )
    except (ClientError, asyncio.TimeoutError) as exc:
        raise GatewayError(502, "upstream_unavailable") from exc


async def _proxy_websocket(request: web.Request) -> web.WebSocketResponse:
    upstream_url = _build_upstream_url(request)
    headers = _selected_headers(request.headers, SAFE_REQUEST_HEADERS)
    session: ClientSession = request.app[SESSION_KEY]

    try:
        upstream = await session.ws_connect(
            upstream_url,
            headers=headers,
            autoping=False,
            autoclose=False,
        )
    except (ClientError, asyncio.TimeoutError) as exc:
        raise GatewayError(502, "upstream_unavailable") from exc

    downstream = web.WebSocketResponse(autoping=False, autoclose=False)
    try:
        await downstream.prepare(request)
    except BaseException:
        try:
            await upstream.close()
        except BaseException:
            pass
        raise
    downstream_task = asyncio.create_task(_forward_websocket(downstream, upstream))
    upstream_task = asyncio.create_task(_forward_websocket(upstream, downstream))
    tasks = {downstream_task, upstream_task}

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        if not downstream.closed:
            await downstream.close()
        if not upstream.closed:
            await upstream.close()

    return downstream


async def _proxy_worker_websocket(
    request: web.Request, local_job_id: str
) -> web.WebSocketResponse:
    remote_job_id = request.app[JOB_MAPPINGS_KEY][local_job_id]
    query = dict(request.query)
    query.pop("job_id", None)
    query["clientId"] = uuid.uuid4().hex
    upstream_url = _build_upstream_url(request, query=query)
    headers = _selected_headers(request.headers, SAFE_REQUEST_HEADERS)
    session: ClientSession = request.app[SESSION_KEY]
    try:
        upstream = await session.ws_connect(
            upstream_url, headers=headers, autoping=False, autoclose=False
        )
    except (ClientError, asyncio.TimeoutError) as exc:
        raise GatewayError(502, "upstream_unavailable") from exc

    downstream = web.WebSocketResponse(autoping=False, autoclose=False)
    try:
        await downstream.prepare(request)
    except BaseException:
        await upstream.close()
        raise

    downstream_task = asyncio.create_task(_forward_websocket(downstream, upstream))
    event_task = asyncio.create_task(
        _forward_worker_events(upstream, downstream, local_job_id, remote_job_id)
    )
    tasks = {downstream_task, event_task}
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        if not downstream.closed:
            await downstream.close()
        if not upstream.closed:
            await upstream.close()
    return downstream


async def _forward_worker_events(
    upstream, downstream, local_job_id: str, remote_job_id: str
) -> None:
    sequence = 0
    try:
        async for message in upstream:
            if message.type == WSMsgType.TEXT:
                try:
                    native = json.loads(message.data)
                except (TypeError, ValueError):
                    continue
                event = _worker_event_from_message(
                    local_job_id, remote_job_id, native, sequence
                )
                if event is not None:
                    await downstream.send_json(event)
                    sequence += 1
            elif message.type == WSMsgType.PING:
                await downstream.ping(message.data)
            elif message.type == WSMsgType.PONG:
                await downstream.pong(message.data)
            elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR, WSMsgType.CLOSING}:
                await downstream.close()
                return
    except asyncio.CancelledError:
        raise
    except (ClientError, ConnectionResetError, RuntimeError):
        await downstream.close()


def _worker_event_from_message(
    local_job_id: str, remote_job_id: str, native: object, sequence: int
) -> dict[str, object] | None:
    if not isinstance(native, dict) or not isinstance(native.get("type"), str):
        return None
    message_type = native["type"]
    data = native.get("data") if isinstance(native.get("data"), dict) else {}
    native_prompt_id = data.get("prompt_id")
    if isinstance(native_prompt_id, str) and native_prompt_id != remote_job_id:
        return None

    state = None
    progress = None
    stage = None
    message = None
    if message_type == "status":
        state = "queued"
        message = "ComfyUI 已接受任务"
    elif message_type == "execution_start":
        state = "running"
        progress = 0.0
        message = "开始执行工作流"
    elif message_type == "progress":
        state = "running"
        value = data.get("value")
        maximum = data.get("max")
        if isinstance(value, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0:
            progress = max(0.0, min(1.0, float(value) / float(maximum)))
        message = "正在采样"
    elif message_type == "executing":
        node = data.get("node")
        if node is None:
            return None
        state = "running"
        stage = str(node)
        message = f"执行节点 {node}"
    elif message_type == "execution_cached":
        state = "running"
        message = "复用缓存节点"
    elif message_type == "execution_success":
        state = "completed"
        progress = 1.0
        message = "视频生成完成"
    elif message_type in {"execution_error", "execution_interrupted"}:
        state = "canceled" if message_type == "execution_interrupted" else "failed"
        message = "任务已中断" if state == "canceled" else "ComfyUI 执行失败"
    else:
        return None

    return {
        "job_id": local_job_id,
        "remote_job_id": remote_job_id,
        "sequence": sequence,
        "state": state,
        "progress": progress,
        "stage": stage,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


async def _forward_websocket(source, target) -> None:
    try:
        async for message in source:
            if message.type == WSMsgType.TEXT:
                await target.send_str(message.data)
            elif message.type == WSMsgType.BINARY:
                await target.send_bytes(message.data)
            elif message.type == WSMsgType.PING:
                await target.ping(message.data)
            elif message.type == WSMsgType.PONG:
                await target.pong(message.data)
            elif message.type == WSMsgType.CLOSE:
                await target.close(code=_close_code(message), message=_close_message(message))
                return
            elif message.type in {WSMsgType.CLOSED, WSMsgType.ERROR, WSMsgType.CLOSING}:
                await target.close()
                return
    except asyncio.CancelledError:
        raise
    except (ClientError, ConnectionResetError, RuntimeError):
        await target.close()


async def _read_bounded_body(request: web.Request, limit: int) -> bytes:
    if request.content_length is not None and request.content_length > limit:
        raise GatewayError(413, "request_body_too_large")

    chunks = []
    total = 0
    while True:
        chunk = await request.content.read(64 * 1024)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise GatewayError(413, "request_body_too_large")
        chunks.append(chunk)


def _rewrite_interrupt_body(request: web.Request, body: bytes) -> bytes:
    if request.path != "/interrupt":
        return body
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return body
    if not isinstance(payload, dict):
        return body
    local_job_id = payload.get("prompt")
    if not isinstance(local_job_id, str):
        return body
    remote_job_id = request.app[JOB_MAPPINGS_KEY].get(local_job_id)
    if not remote_job_id:
        return body
    payload["prompt"] = remote_job_id
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _build_upstream_url(request: web.Request, *, path: str | None = None, query=None) -> URL:
    upstream = request.app[CONFIG_KEY].upstream_url
    base_path = upstream.path.rstrip("/")
    request_path = path or request.path
    upstream_path = f"{base_path}{request_path}" or "/"
    return upstream.with_path(upstream_path).with_query(request.query if query is None else query)


def _selected_headers(headers: Iterable, allowed: frozenset) -> dict:
    return {key: value for key, value in headers.items() if key.lower() in allowed}


def _close_code(message) -> int:
    return message.data if isinstance(message.data, int) else 1000


def _close_message(message) -> bytes:
    extra = message.extra or ""
    return extra.encode("utf-8", errors="ignore")


async def _client_session_context(app: web.Application):
    app[SESSION_KEY] = ClientSession(auto_decompress=False)
    yield
    await app[SESSION_KEY].close()


def _load_bootstrap_status(path: Path) -> dict:
    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("status is not an object")
        stage = payload.get("stage")
        progress = payload.get("progress")
        message = payload.get("message")
        ready = payload.get("ready")
        error = payload.get("error")
        updated_at = payload.get("updated_at")
        if (
            not isinstance(stage, str)
            or not stage
            or not isinstance(progress, (int, float))
            or not 0 <= progress <= 1
            or not isinstance(message, str)
            or not message
            or not isinstance(ready, bool)
            or (error is not None and not isinstance(error, str))
            or not isinstance(updated_at, str)
            or not updated_at
        ):
            raise ValueError("status fields are invalid")
        return {
            "stage": stage,
            "progress": progress,
            "message": message,
            "ready": ready,
            "error": error,
            "updated_at": updated_at,
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {
            "stage": "waiting_for_base",
            "progress": 0,
            "message": "等待基础环境初始化",
            "ready": False,
            "error": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def _bootstrap_log_cursor(request: web.Request) -> tuple[int, int, str]:
    try:
        offset = int(request.query.get("offset", "0"))
        limit = int(request.query.get("limit", str(DEFAULT_BOOTSTRAP_LOG_LINES)))
    except (TypeError, ValueError) as exc:
        raise GatewayError(400, "invalid_bootstrap_log_cursor") from exc
    generation = request.query.get("generation", "")
    if offset < 0 or not 1 <= limit <= MAX_BOOTSTRAP_LOG_LINES or len(generation) > 256:
        raise GatewayError(400, "invalid_bootstrap_log_cursor")
    return offset, limit, generation


def _bootstrap_log_generation(stream, offset: int) -> str:
    stat = os.fstat(stream.fileno())
    current_position = stream.tell()
    stream.seek(0)
    digest = hashlib.sha256(stream.read(offset)).hexdigest()
    stream.seek(current_position)
    return f"{stat.st_dev}:{stat.st_ino}:{digest}"


def _load_bootstrap_log(path: Path, offset: int, limit: int, generation: str) -> dict:
    try:
        with path.open("rb") as stream:
            size = os.fstat(stream.fileno()).st_size
            reset = offset > size or (bool(generation) and generation != _bootstrap_log_generation(stream, offset))
            if reset:
                offset = 0
            stream.seek(offset)
            payload = stream.read(MAX_BOOTSTRAP_LOG_BYTES)
            has_more_bytes = stream.tell() < os.fstat(stream.fileno()).st_size
            if not payload:
                next_offset = offset
                return {
                    "lines": [],
                    "next_offset": next_offset,
                    "generation": _bootstrap_log_generation(stream, next_offset),
                    "truncated": False,
                    "reset": reset,
                }

            last_newline = payload.rfind(b"\n")
            if last_newline < 0:
                return {
                    "lines": [],
                    "next_offset": offset,
                    "generation": _bootstrap_log_generation(stream, offset),
                    "truncated": False,
                    "reset": reset,
                }

            complete = payload[: last_newline + 1]
            raw_lines = complete.splitlines(keepends=True)
            selected = raw_lines[:limit]
            next_offset = offset + sum(len(line) for line in selected)
            return {
                "lines": [line.decode("utf-8", errors="replace").rstrip("\r\n") for line in selected],
                "next_offset": next_offset,
                "generation": _bootstrap_log_generation(stream, next_offset),
                "truncated": len(raw_lines) > limit or has_more_bytes,
                "reset": reset,
            }
    except OSError:
        return {"lines": [], "next_offset": offset, "generation": "", "truncated": False, "reset": False}


def _error_text(error: str, status: int, request_id: str) -> str:
    return json.dumps(
        {"error": error, "request_id": request_id, "status": status},
        sort_keys=True,
    )


def _error_response(
    error: str, status: int, request_id: str, headers=None
) -> web.Response:
    response = web.Response(
        status=status,
        text=_error_text(error, status, request_id),
        content_type="application/json",
    )
    if headers:
        response.headers.update(headers)
    return response


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authenticated H3 Worker gateway")
    parser.add_argument(
        "--token",
        "--worker-token",
        dest="token",
        default=os.getenv("H3_WORKER_TOKEN", ""),
    )
    parser.add_argument(
        "--max-upload-bytes",
        type=_positive_int,
        default=os.getenv("H3_GATEWAY_MAX_UPLOAD_BYTES", str(DEFAULT_MAX_UPLOAD_BYTES)),
    )
    parser.add_argument(
        "--upstream",
        default=os.getenv("H3_GATEWAY_UPSTREAM_URL", "http://127.0.0.1:8188"),
    )
    parser.add_argument(
        "--listen",
        default=os.getenv("H3_GATEWAY_LISTEN", "0.0.0.0"),
    )
    parser.add_argument(
        "--port",
        type=_positive_int,
        default=os.getenv("H3_GATEWAY_PORT", "8190"),
    )
    parser.add_argument(
        "--runtime-config",
        default=os.getenv("H3_RUNTIME_CONFIG", "/run/h3/runtime.json"),
    )
    parser.add_argument(
        "--template-catalog",
        default=os.getenv("H3_TEMPLATE_CATALOG", "/opt/h3/config/templates.json"),
    )
    parser.add_argument(
        "--comfyui-dir",
        default=os.getenv("COMFYUI_DIR", "/opt/ComfyUI"),
    )
    parser.add_argument(
        "--bootstrap-status",
        default=os.getenv("H3_BOOTSTRAP_STATUS_FILE", "/run/h3/bootstrap.json"),
    )
    parser.add_argument(
        "--bootstrap-log",
        default=os.getenv("H3_BOOTSTRAP_LOG_FILE", "/var/log/h3/bootstrap.log"),
    )
    return parser


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.token or not args.token.strip():
        parser.error("H3_WORKER_TOKEN must not be empty")

    try:
        app = create_app(
            token=args.token,
            upstream_url=args.upstream,
            max_upload_bytes=args.max_upload_bytes,
            runtime_config_path=args.runtime_config,
            template_catalog_path=args.template_catalog,
            comfyui_dir=args.comfyui_dir,
            bootstrap_status_path=args.bootstrap_status,
            bootstrap_log_path=args.bootstrap_log,
        )
    except ValueError as exc:
        parser.error(str(exc))

    web.run_app(app, host=args.listen, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())

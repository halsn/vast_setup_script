import argparse
import asyncio
from datetime import datetime, timezone
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


ALLOWED_PATHS = frozenset(
    {
        "/healthz",
        "/bootstrap/status",
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


CONFIG_KEY = web.AppKey("gateway_config", GatewayConfig)
SESSION_KEY = web.AppKey("http_session", ClientSession)


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
    )
    app = web.Application(middlewares=[_gateway_middleware], client_max_size=None)
    app[CONFIG_KEY] = config
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
    if request.path == "/ws":
        return await _proxy_websocket(request)
    return await _proxy_http(request)


def _is_allowed_path(path: str) -> bool:
    if path in ALLOWED_PATHS:
        return True
    if path.startswith("/history/"):
        prompt_id = path.removeprefix("/history/")
        return bool(prompt_id) and "/" not in prompt_id
    return False


async def _proxy_http(request: web.Request) -> web.StreamResponse:
    body = await _read_bounded_body(request, request.app[CONFIG_KEY].max_upload_bytes)
    upstream_url = _build_upstream_url(request)
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


def _build_upstream_url(request: web.Request) -> URL:
    upstream = request.app[CONFIG_KEY].upstream_url
    base_path = upstream.path.rstrip("/")
    path = f"{base_path}{request.path}" or "/"
    return upstream.with_path(path).with_query(request.query)


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
        )
    except ValueError as exc:
        parser.error(str(exc))

    web.run_app(app, host=args.listen, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())

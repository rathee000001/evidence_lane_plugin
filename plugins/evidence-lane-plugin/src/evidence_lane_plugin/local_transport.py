"""Bounded loopback transport. Local credentials never require a tunnel key."""

from __future__ import annotations

import hmac
import json
import platform
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Self
from urllib.parse import parse_qs, urlsplit

import httpx
from pydantic import ValidationError

from .capture_routing import HookEnvelope
from .connections import ConnectRequest, Session
from .endpoint_credentials import read_endpoint, write_endpoint
from .engine import Engine
from .errors import LaneError
from .internal_sdk import PublicActionSDKDispatcher
from .sdk import ActionRequest, ActionResponse
from .storage import json_text, reject_links
from .studio_gateway import SessionRequest, StudioGateway

MAX_MESSAGE_BYTES = 1_048_576
# Authenticated action frames carry task-budgeted artifacts plus envelope
# overhead. Connection, owner-control and Studio routes retain the small cap.
MAX_ACTION_MESSAGE_BYTES = 67_174_400


def owner_endpoint(runtime_root: Path) -> tuple[dict, str]:
    """Read the protected current-user endpoint inside the local adapter only."""
    root = runtime_root.expanduser().absolute()
    path = root / 'endpoint.json'
    reject_links(path, Path(root.anchor))
    try:
        return read_endpoint(path)
    except (OSError, ValueError, KeyError, TypeError):
        raise LaneError('ENGINE_UNAVAILABLE', 'Start a compatible local engine first.') from None


class _BoundedServer(ThreadingHTTPServer):
    daemon_threads = False
    block_on_close = True
    allow_reuse_address = False

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, handler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Do not print user request data or raw exception strings into a shared log.
        return


class LocalEndpoint:
    def __init__(self, engine: Engine, *, owner_control=None, studio_enabled=None):
        self.engine = engine
        self.owner_control = owner_control
        enabled = platform.system() == 'Windows' if studio_enabled is None else studio_enabled
        if enabled and platform.system() != 'Windows':
            raise LaneError('STUDIO_PLATFORM_UNSUPPORTED', 'Studio is available on Windows PCs only.')
        self.studio = StudioGateway(engine, owner_control=owner_control) if enabled else None
        self.token = secrets.token_urlsafe(48)
        self.path = engine.root / "endpoint.json"
        self.server: _BoundedServer | None = None
        self.thread: threading.Thread | None = None

    def action(self, request: ActionRequest, session: Session) -> ActionResponse:
        return PublicActionSDKDispatcher(self.engine).execute(request, session)

    def start(self) -> None:
        if self.engine.phase != "running" or self.server is not None:
            raise LaneError("INVALID_ENDPOINT_TRANSITION", "Start one endpoint on a running engine.")
        endpoint = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "EvidenceLane/4"
            sys_version = ""

            def log_message(self, *args):
                pass

            def reply(self, status, payload, *, cookie=None):
                content = json_text(payload).encode("utf-8")
                limit = MAX_ACTION_MESSAGE_BYTES if self.path == '/v4/action' else MAX_MESSAGE_BYTES
                if len(content) > limit:
                    status, content = 500, b'{"error":"RESPONSE_TOO_LARGE"}'
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(content)
                self.close_connection = True

            def reject_unread_request(self, status: int, error: str) -> None:
                # Windows may reset a connection closed with an unread body before
                # its 401/403 reaches the caller. Drain only a valid bounded body.
                lengths = self.headers.get_all("Content-Length", [])
                if (not self.headers.get("Transfer-Encoding") and len(lengths) == 1
                        and lengths[0].isdigit() and 0 <= int(lengths[0]) <= MAX_MESSAGE_BYTES):
                    remaining = int(lengths[0])
                    while remaining:
                        chunk = self.rfile.read(min(8192, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                self.reply(status, {"error": error})

            def do_GET(self):
                try:
                    if endpoint.studio is None:
                        self.reply(404, {'error': 'STUDIO_UNAVAILABLE_ON_THIS_ROUTE'})
                        return
                    expected_host = f"127.0.0.1:{self.server.server_port}"
                    if self.headers.get_all("Host") != [expected_host]:
                        self.reply(403, {"error": "LOCAL_ORIGIN_REQUIRED"})
                        return
                    target = urlsplit(self.path)
                    from .studio_assets import static_asset
                    asset = static_asset(target.path) if not target.query else None
                    if asset is not None:
                        content, kind = asset
                        self.send_response(200)
                        self.send_header("Content-Type", kind)
                        self.send_header("Content-Length", str(len(content)))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.send_header("Referrer-Policy", "no-referrer")
                        self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
                        self.send_header("Connection", "close")
                        self.end_headers()
                        self.wfile.write(content)
                        self.close_connection = True
                        return
                    if target.path != "/studio/api/snapshot":
                        self.reply(404, {"error": "UNKNOWN_ROUTE"})
                        return
                    # Fetch's custom header needs a preflight from another origin.
                    # This server never enables CORS or responds to that preflight.
                    if (self.headers.get_all("X-Studio-Read") != ["1"]
                            or self.headers.get_all("Origin", []) not in ([], ["http://" + expected_host])):
                        self.reply(403, {"error": "LOCAL_ORIGIN_REQUIRED"})
                        return
                    endpoint.studio.authenticate(self.headers.get("Cookie", ""))
                    query = parse_qs(target.query, strict_parsing=True, max_num_fields=1)
                    if set(query) - {"project_id"}:
                        raise ValueError()
                    project_id = query.get("project_id", [None])[0]
                    self.reply(200, endpoint.studio.snapshot(project_id))
                except LaneError as error:
                    self.reply(401 if error.code == "STUDIO_AUTHENTICATION_REQUIRED" else 409, {"error": error.code})
                except (ValueError, UnicodeError):
                    self.reply(400, {"error": "INVALID_MESSAGE"})
                except (TimeoutError, ConnectionError):
                    self.close_connection = True
                except Exception:  # noqa: BLE001 - no request details in shared output
                    self.reply(500, {"error": "INTERNAL_ERROR"})

            def studio_post(self):
                if endpoint.studio is None:
                    self.reject_unread_request(404, 'STUDIO_UNAVAILABLE_ON_THIS_ROUTE')
                    return
                origin = f"http://127.0.0.1:{self.server.server_port}"
                if (self.headers.get_all("Host") != [origin[7:]]
                        or self.headers.get_all("Origin") != [origin]
                        or self.headers.get("Authorization")):
                    self.reject_unread_request(403, "LOCAL_ORIGIN_REQUIRED")
                    return
                lengths = self.headers.get_all("Content-Length", [])
                if (self.headers.get("Transfer-Encoding") or len(lengths) != 1
                        or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= MAX_MESSAGE_BYTES):
                    self.reply(400, {"error": "INVALID_MESSAGE_LENGTH"})
                    return
                if self.headers.get_all("Content-Type") != ["application/json"]:
                    self.reject_unread_request(415, "JSON_REQUIRED")
                    return
                raw = self.rfile.read(int(lengths[0]))
                if len(raw) != int(lengths[0]):
                    raise ValueError()
                if self.path == "/studio/api/session":
                    request = SessionRequest.model_validate_json(raw)
                    cookie = None
                    if request.ticket:
                        token, session = endpoint.studio.exchange(request.ticket, cookie=self.headers.get("Cookie", ""))
                        cookie = endpoint.studio.cookie(token) if token else None
                    else:
                        session = endpoint.studio.authenticate(self.headers.get("Cookie", ""))
                    self.reply(200, {"csrf": session.csrf, "expires_at": session.expires_at.isoformat()}, cookie=cookie)
                    return
                csrf = self.headers.get_all("X-CSRF-Token", [])
                if len(csrf) != 1:
                    self.reply(403, {"error": "STUDIO_CSRF_REQUIRED"})
                    return
                session = endpoint.studio.authenticate(self.headers.get("Cookie", ""), csrf=csrf[0])
                payload = json.loads(raw)
                if not isinstance(payload, dict):
                    raise TypeError()
                if self.path == "/studio/api/logout" and payload == {}:
                    endpoint.studio.logout(session)
                    self.reply(200, {"logged_out": True}, cookie=f"{endpoint.studio.cookie_name}=; HttpOnly; SameSite=Strict; Path=/studio/; Max-Age=0")
                    return
                routes = {"/studio/api/" + name: name for name in
                          ("read", "backup-verify")}
                if self.path not in routes:
                    self.reply(404, {"error": "UNKNOWN_ROUTE"})
                    return
                self.reply(200, endpoint.studio.command(routes[self.path], payload, session))

            def do_POST(self):
                try:
                    if self.path.startswith("/studio/"):
                        self.studio_post()
                        return
                    expected_host = f"127.0.0.1:{self.server.server_port}"
                    if self.headers.get_all("Host") != [expected_host] or self.headers.get("Origin"):
                        self.reject_unread_request(403, "LOCAL_ORIGIN_REQUIRED")
                        return
                    credentials = self.headers.get_all("Authorization", [])
                    if len(credentials) != 1 or not credentials[0].startswith("Bearer "):
                        self.reject_unread_request(401, "AUTHENTICATION_REQUIRED")
                        return
                    token = credentials[0][7:]
                    session = None
                    if self.path in {"/v4/connect", "/v4/control", "/v4/capture"}:
                        if not hmac.compare_digest(token.encode(), endpoint.token.encode()):
                            self.reject_unread_request(401, "AUTHENTICATION_REQUIRED")
                            return
                    else:
                        try:
                            session = endpoint.engine.clients.authenticate(token)
                        except LaneError:
                            self.reject_unread_request(401, "AUTHENTICATION_REQUIRED")
                            return
                    lengths = self.headers.get_all("Content-Length", [])
                    if (self.headers.get("Transfer-Encoding") or len(lengths) != 1
                            or not lengths[0].isdigit()):
                        self.reply(400, {"error": "INVALID_MESSAGE_LENGTH"})
                        return
                    length = int(lengths[0])
                    limit = MAX_ACTION_MESSAGE_BYTES if self.path == '/v4/action' else MAX_MESSAGE_BYTES
                    if not 0 < length <= limit:
                        self.reply(413, {"error": "MESSAGE_TOO_LARGE"})
                        return
                    if self.headers.get("Content-Type") != "application/json":
                        self.reply(415, {"error": "JSON_REQUIRED"})
                        return
                    raw = self.rfile.read(length)
                    if len(raw) != length:
                        self.reply(400, {"error": "INCOMPLETE_MESSAGE"})
                        return
                    if self.path == "/v4/connect":
                        connect = ConnectRequest.model_validate_json(raw)
                        with endpoint.engine.admit():
                            session_token, connected = endpoint.engine.clients.connect(connect)
                        self.reply(200, {"instance_id": endpoint.engine.instance_id,
                                         "client_id": connected.client_id, "token": session_token})
                    elif self.path == "/v4/capture":
                        request = json.loads(raw)
                        if (not isinstance(request, dict) or set(request) != {"instance_id", "capture"}
                                or request["instance_id"] != endpoint.engine.instance_id):
                            self.reply(400, {"error": "INVALID_CAPTURE_BINDING"})
                            return
                        result = endpoint.engine.capture.capture(HookEnvelope.model_validate(request["capture"]))
                        self.reply(200, result.model_dump(mode="json"))
                    elif self.path == "/v4/control":
                        request = json.loads(raw)
                        if (not isinstance(request, dict) or set(request) != {"operation", "instance_id"}
                                or request["instance_id"] != endpoint.engine.instance_id
                                or not isinstance(request["operation"], str)
                                or request["operation"] not in {"open_studio", "shutdown"}):
                            self.reply(400, {"error": "INVALID_OWNER_CONTROL"})
                            return
                        if endpoint.owner_control is None:
                            self.reply(409, {"error": "OWNER_CONTROL_UNAVAILABLE"})
                            return
                        self.reply(200, endpoint.owner_control(request["operation"]))
                    elif self.path == "/v4/disconnect":
                        if json.loads(raw) != {}:
                            raise ValueError()
                        endpoint.engine.clients.disconnect(token)
                        self.reply(200, {"disconnected": True})
                    elif self.path == "/v4/catalog":
                        if json.loads(raw) != {}:
                            raise ValueError()
                        self.reply(200, {"instance_id": endpoint.engine.instance_id,
                                         "actions": endpoint.engine.registry.schemas()})
                    elif self.path == "/v4/action":
                        request = ActionRequest.model_validate_json(raw)
                        self.reply(200, endpoint.action(request, session).model_dump(mode="json"))
                    else:
                        self.reply(404, {"error": "UNKNOWN_ROUTE"})
                except (ValueError, TypeError, ValidationError, UnicodeError):
                    self.reply(400, {"error": "INVALID_MESSAGE"})
                except LaneError as error:
                    self.reply(409, {"error": error.code})
                except (TimeoutError, ConnectionError):
                    self.close_connection = True
                except Exception:  # noqa: BLE001 - transport boundary must not expose handler inputs
                    self.reply(500, {"error": "INTERNAL_ERROR"})

        server = _BoundedServer(("127.0.0.1", 0), Handler)
        try:
            reject_links(self.path, self.engine.root)
            write_endpoint(self.path, {
                "protocol_version": 4, "instance_id": self.engine.instance_id,
                "port": server.server_port,
            }, self.token)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05},
                                      name="evidence-lane-local-api", daemon=False)
            thread.start()
            self.server, self.thread = server, thread
        except BaseException:
            server.server_close()
            raise

    def close(self) -> None:
        if self.server is None:
            return
        self.server.shutdown()
        self.server.server_close()
        if self.thread is None:
            raise LaneError('ENDPOINT_THREAD_UNAVAILABLE', 'The active endpoint has no owned server thread to join.')
        self.thread.join(timeout=10)
        if self.studio is not None:
            self.studio.close()
        reject_links(self.path, self.engine.root)
        self.path.unlink(missing_ok=True)
        self.server, self.thread = None, None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *args) -> None:
        self.close()


class LocalTransport:
    def __init__(self, runtime_root: Path, *, timeout: float = 20,
                 connection: ConnectRequest | None = None):
        record, token = owner_endpoint(runtime_root)
        self.instance_id = record['instance_id']
        self.base_url = f"http://127.0.0.1:{record['port']}"
        self.http = httpx.Client(
            base_url=self.base_url, headers={"Authorization": f"Bearer {token}"},
            timeout=timeout, trust_env=False, follow_redirects=False,
        )
        try:
            connected = self.post("/v4/connect", (connection or ConnectRequest()).model_dump(mode="json"))
            if connected["instance_id"] != self.instance_id:
                raise LaneError("ENGINE_INSTANCE_CHANGED", "Reconnect to the current engine instance.")
            self.client_id = connected["client_id"]
            self.http.headers["Authorization"] = f"Bearer {connected['token']}"
        except BaseException:
            self.http.close()
            raise

    def post(self, path: str, payload: dict) -> dict:
        # Never automatically reconnect or retry a mutation after a lost response.
        try:
            with self.http.stream("POST", path, json=payload) as response:
                response.raise_for_status()
                content = bytearray()
                for chunk in response.iter_bytes():
                    content.extend(chunk)
                    limit = MAX_ACTION_MESSAGE_BYTES if path == '/v4/action' else MAX_MESSAGE_BYTES
                    if len(content) > limit:
                        raise LaneError("RESPONSE_TOO_LARGE", "The local response exceeded its budget.")
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise TypeError()
                return result
        except (httpx.HTTPError, ValueError, TypeError):
            raise LaneError(
                "LOCAL_TRANSPORT_FAILED", "The engine request failed; reconcile before retrying work."
            ) from None

    def catalog(self) -> list[dict]:
        response = self.post("/v4/catalog", {})
        if response.get("instance_id") != self.instance_id:
            raise LaneError("ENGINE_INSTANCE_CHANGED", "Reconnect to the current engine instance.")
        return response["actions"]

    def send(self, request: ActionRequest) -> ActionResponse:
        return ActionResponse.model_validate(self.post("/v4/action", request.model_dump(mode="json")))

    def close(self) -> None:
        try:
            if not self.http.is_closed:
                self.post("/v4/disconnect", {})
        except LaneError:
            pass  # A disconnected engine already invalidates its volatile client sessions.
        finally:
            self.http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args) -> None:
        self.close()

"""Persistent per-user service, with explicit Studio launch and crash reconciliation."""

from __future__ import annotations

import argparse
import json
import platform
import signal
import threading
from pathlib import Path

import httpx

from .engine_runtime import create_runtime_engine, reconcile_jobs
from .errors import LaneError
from .local_transport import LocalEndpoint, owner_endpoint
from .studio_window import StudioWindow


def request_owner_control(runtime_root: Path, operation: str) -> dict:
    """Current-user launcher control; no retry after an uncertain response."""
    if operation not in {"open_studio", "shutdown"}:
        raise LaneError("UNKNOWN_OWNER_OPERATION", "The launcher operation is unsupported.")
    try:
        record, credential = owner_endpoint(runtime_root)
        with (httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client,
              client.stream("POST", f"http://127.0.0.1:{record['port']}/v4/control",
                            headers={"Authorization": "Bearer " + credential},
                            json={"operation": operation, "instance_id": record["instance_id"]}) as response):
            response.raise_for_status()
            content = bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content) > 65_536:
                    raise ValueError()
            result = json.loads(content)
            if not isinstance(result, dict):
                raise TypeError()
            return result
    except (OSError, KeyError, ValueError, TypeError, httpx.HTTPError):
        raise LaneError("OWNER_CONTROL_FAILED", "The local engine control request was not confirmed.") from None


class Service:
    def __init__(self, runtime_root: Path, *, workers: int = 2, capabilities=None, studio_launcher=None):
        if platform.system() != 'Windows':
            raise LaneError('STUDIO_PLATFORM_UNSUPPORTED',
                            'The Studio service and managed local toolchain are available on Windows PCs only.')
        self.engine = create_runtime_engine(runtime_root, workers=workers, capabilities=capabilities)
        self.endpoint = LocalEndpoint(self.engine, owner_control=self.control)
        self.studio_launcher = studio_launcher or StudioWindow(runtime_root)
        self.stop_requested = threading.Event()
        self.recovery: list[dict] = []
        self.studio_launch = "not_requested"

    def reconcile(self) -> None:
        """Reconcile previously admitted jobs only, without replaying any effect."""
        self.recovery = reconcile_jobs(self.engine)

    def start(self, *, open_studio: bool = False) -> None:
        self.engine.start()
        try:
            self.reconcile()
            self.endpoint.start()
            if open_studio:
                self.open_studio()
        except BaseException:
            self.endpoint.close()
            self.engine.stop()
            raise

    def open_studio(self) -> dict:
        if self.endpoint.server is None or self.endpoint.studio is None:
            raise LaneError("STUDIO_ENDPOINT_UNAVAILABLE", "Start the local endpoint before opening Studio.")
        url = f"http://127.0.0.1:{self.endpoint.server.server_port}/studio/"
        opened = self.studio_launcher(url + "#ticket=" + self.endpoint.studio.issue_ticket())
        self.studio_launch = "requested" if opened else "launcher_unavailable"
        return {"studio_launch": self.studio_launch, "url": url, "visible_window_verified": False}

    def control(self, operation: str) -> dict:
        if operation == "open_studio":
            return self.open_studio()
        if operation == "shutdown":
            self.stop_requested.set()
            return {"shutdown_requested": True, "stopped": False}
        raise LaneError("UNKNOWN_OWNER_OPERATION", "This engine control operation is unsupported.")

    def close(self) -> None:
        if self.engine.phase == "running":
            self.engine.begin_drain()
        self.endpoint.close()
        if self.engine.phase in {"running", "draining"}:
            self.engine.stop()

    def wait(self) -> None:
        self.stop_requested.wait()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-root", type=Path)
    parser.add_argument("--control", choices=("open_studio", "shutdown"))
    args = parser.parse_args(argv)
    from .launcher import runtime_root
    args.runtime_root = runtime_root(args.runtime_root)
    if args.control:
        request_owner_control(args.runtime_root, args.control)
        return
    if platform.system() != "Windows":
        raise LaneError(
            "WINDOWS_HOST_REQUIRED",
            "Evidence Lane supports persistent local Windows Codex Desktop hosts.",
        )
    service = Service(args.runtime_root)
    signal.signal(signal.SIGINT, lambda *_: service.stop_requested.set())
    signal.signal(signal.SIGTERM, lambda *_: service.stop_requested.set())
    try:
        try:
            # Engine discovery from MCP or another client must not create a
            # Studio window. The installed launcher requests one window only
            # after the engine endpoint is ready.
            service.start(open_studio=False)
        except LaneError as error:
            if error.code != "RUNTIME_IN_USE":
                raise
            if platform.system() == 'Windows':
                request_owner_control(args.runtime_root, "open_studio")
            return
        service.wait()
    finally:
        service.close()


if __name__ == "__main__":
    main()

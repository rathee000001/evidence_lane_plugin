"""Optional explicit HTTPS deployment; never enabled by normal local startup."""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn

from .engine_runtime import create_runtime_engine, reconcile_jobs
from .errors import LaneError
from .remote_api import RemoteApplication, RemoteGateway, RemotePolicy
from .storage import reject_links


def server_config(gateway: RemoteGateway, *, certificate: Path, private_key: Path,
                  bind: str = "127.0.0.1") -> uvicorn.Config:
    for path in (certificate, private_key):
        if not path.is_absolute():
            raise LaneError("REMOTE_TLS_FILE_INVALID", "Use absolute TLS certificate and private-key paths.")
        reject_links(path, Path(path.anchor))
        if not path.is_file():
            raise LaneError("REMOTE_TLS_FILE_INVALID", "The selected TLS file is unavailable.")
    # No trusted forwarded headers, reload, auto exposure, access logging or
    # WebSockets. uvicorn's TLS context keeps Python's TLS 1.2 minimum.
    config = uvicorn.Config(
        RemoteApplication(gateway), host=bind, port=urlsplit(gateway.policy.origin).port or 443,
        ssl_certfile=str(certificate), ssl_keyfile=str(private_key),
        proxy_headers=False, forwarded_allow_ips="", access_log=False, log_config=None,
        log_level="critical", server_header=False, date_header=False, lifespan="off",
        ws="none", interface="asgi3", http="h11", limit_concurrency=16,
        timeout_keep_alive=2, timeout_graceful_shutdown=30, h11_max_incomplete_event_size=16_384,
    )
    return config


def main():
    parser = argparse.ArgumentParser(description="Run an explicitly configured Evidence Lane HTTPS engine")
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--tls-certificate", type=Path, required=True)
    parser.add_argument("--tls-private-key", type=Path, required=True)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    path = args.policy.absolute()
    reject_links(path, Path(path.anchor))
    if path.stat().st_size > 262_144:
        raise LaneError("REMOTE_POLICY_INVALID", "The remote policy exceeds its byte budget.")
    policy = RemotePolicy.model_validate_json(path.read_bytes())
    with create_runtime_engine(args.runtime_root, workers=args.workers) as engine:
        reconcile_jobs(engine)
        gateway = RemoteGateway(engine, policy)
        server = uvicorn.Server(server_config(gateway, certificate=args.tls_certificate,
                                             private_key=args.tls_private_key, bind=args.bind))
        try:
            server.run()
        finally:
            if engine.phase == 'running':
                engine.begin_drain()


if __name__ == "__main__":
    main()

"""Hidden Codex-only FastAPI runtime used by the private tunnel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .native_toolchain import validate_hidden_runtime_root
from .runtime_toolchain import inspect_runtime_toolchain


class RuntimeApiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plugin_root: Path
    hidden_state_root: Path
    host_profile: str
    bind_host: str = "127.0.0.1"
    bind_port: int = Field(default=8765, ge=1024, le=65535)
    max_upload_bytes: int = Field(default=64 * 1024 * 1024, ge=1024)


def load_runtime_api_settings() -> RuntimeApiSettings:
    """Load typed production settings; no dotenv or workspace fallback."""

    from pydantic_settings import BaseSettings, SettingsConfigDict

    class _Settings(BaseSettings):
        model_config = SettingsConfigDict(
            env_prefix="EVIDENCE_LANE_",
            extra="ignore",
            case_sensitive=False,
        )

        plugin_root: Path
        hidden_state_root: Path
        host_profile: str
        bind_host: str = "127.0.0.1"
        bind_port: int = 8765
        max_upload_bytes: int = 64 * 1024 * 1024

    loaded = _Settings()
    return RuntimeApiSettings.model_validate(loaded.model_dump())


def load_maintainer_dotenv(dotenv_path: str | Path) -> dict[str, Any]:
    """Explicit maintainer-only dotenv loader; never called by production boot."""

    path = Path(dotenv_path).resolve(strict=True)
    from dotenv import dotenv_values  # type: ignore[import-not-found]

    values = {str(key): str(value) for key, value in dotenv_values(path).items() if value}
    core = {
        "schema": "evidence-lane.maintainer-dotenv.v1",
        "status": "PASS",
        "path_sha256": sha256_bytes(str(path).encode("utf-8")),
        "file_sha256": sha256_file(path),
        "key_names": sorted(values),
        "values_returned": False,
        "production_runtime_used": False,
    }
    return {**core, "receipt_sha256": sha256_bytes(canonical_json_bytes(core))}


def create_runtime_api(settings: RuntimeApiSettings) -> Any:
    import orjson  # type: ignore[import-not-found]
    from fastapi import FastAPI, File, HTTPException
    from fastapi.responses import Response

    exact_host = settings.host_profile.strip().upper()
    if exact_host not in {"CODEX_DESKTOP", "CODEX_CLI", "CODEX_VM"}:
        raise ValueError("RUNTIME_API_CODEX_HOST_PROFILE_REQUIRED")
    if settings.bind_host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("RUNTIME_API_LOOPBACK_BIND_REQUIRED")
    plugin_root = settings.plugin_root.resolve(strict=True)
    state_root = validate_hidden_runtime_root(settings.hidden_state_root)
    state_root.mkdir(parents=True, exist_ok=True)
    staging = state_root / "source-intake-staging"
    staging.mkdir(parents=True, exist_ok=True)
    app = FastAPI(
        title="Evidence Lane Hidden Codex Runtime",
        version="3.0.0",
    )
    source_upload = File(...)

    def json_response(value: Any) -> Response:
        return Response(
            content=orjson.dumps(value, option=orjson.OPT_SORT_KEYS),
            media_type="application/json",
        )

    @app.get("/health")
    async def health() -> Response:
        return json_response({
            "status": "PASS",
            "plane": "CODEX",
            "host_profile": exact_host,
            "loopback_only": True,
            "workspace_runtime": False,
        })

    @app.get("/toolchain")
    async def toolchain() -> Response:
        return json_response(inspect_runtime_toolchain(plugin_root))

    @app.post("/source-intake/stage")
    async def stage_source(file: Any = source_upload) -> Response:
        import aiofiles  # type: ignore[import-not-found]

        safe_name = Path(file.filename or "source.bin").name
        target = (staging / safe_name).resolve()
        try:
            target.relative_to(staging.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="SOURCE_NAME_INVALID") from exc
        size = 0
        exceeded = False
        async with aiofiles.open(target, "wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    exceeded = True
                    break
                await handle.write(chunk)
        if exceeded:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=413, detail="SOURCE_BOUND_EXCEEDED")
        receipt = {
            "schema": "evidence-lane.runtime-source-stage.v1",
            "status": "STAGED_NOT_INGESTED",
            "name": safe_name,
            "size_bytes": size,
            "sha256": sha256_file(target),
            "hidden_runtime_staging": True,
            "workspace_written": False,
            "project_authority_mutated": False,
        }
        return json_response({
            **receipt,
            "receipt_sha256": sha256_bytes(canonical_json_bytes(receipt)),
        })

    return app


def run_hidden_runtime_api(settings: RuntimeApiSettings) -> None:
    """Run the loopback API under Uvicorn; called only by the tunnel host."""

    import uvicorn  # type: ignore[import-not-found]

    app = create_runtime_api(settings)
    uvicorn.run(
        app,
        host=settings.bind_host,
        port=settings.bind_port,
        log_level="warning",
        access_log=False,
    )


__all__ = [
    "RuntimeApiSettings",
    "create_runtime_api",
    "load_maintainer_dotenv",
    "load_runtime_api_settings",
    "run_hidden_runtime_api",
]

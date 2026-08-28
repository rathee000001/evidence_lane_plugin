"""Bounded file-backed conversation-memory routing authority.

``MEMORY.md`` is kept separate from Evidence Lane Project Memory.  This module
does not claim that the Codex host natively recognizes the filename and it does
not replace host compaction.  It resolves a private, task-bound continuation
guide and exposes only content hashes plus an exact source-chain receipt to
public SDK/MCP surfaces.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes
from .timeutil import utc_now

CONVERSATION_MEMORY_SCHEMA = "evidence-lane.conversation-memory-authority.v1"
CONVERSATION_MEMORY_CHAIN_SCHEMA = "evidence-lane.conversation-memory-source-chain.v1"
DEFAULT_CONVERSATION_MEMORY_MAX_BYTES = 32 * 1024
USER_OBSERVED_OPTIMIZED_CONVERSATION_ARTIFACT_SHA256 = (
    "C0F771753A34C19CE49A1B21459C13A9330A29BCE7183F010E3027DC5F22EC5D"
)

_PRIMARY_FILENAMES = ("MEMORY.override.md", "MEMORY.md")
_SAFE_FALLBACK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_OPTIONAL_EXECUTION_PROFILE_FIELDS = (
    "submodel",
    "reasoning_effort",
    "reasoning_speed",
)


def _exact_text(value: object, *, field: str, max_length: int = 512) -> str:
    exact = str(value or "").strip()
    require(
        bool(exact) and len(exact) <= max_length,
        "CONVERSATION_MEMORY_BINDING_INVALID",
        "MEMORY.md resolution requires every exact runtime binding field.",
        status="MISMATCH",
        field=field,
    )
    return exact


def _bounded_execution_profile(value: Mapping[str, Any]) -> dict[str, str]:
    """Bind available host selectors without inventing unavailable fields."""

    profile = {"model": _exact_text(value.get("model"), field="model")}
    for field in _OPTIONAL_EXECUTION_PROFILE_FIELDS:
        if field not in value or value[field] is None:
            continue
        profile[field] = _exact_text(value[field], field=field)
    return profile


def _contained(path: Path, root: Path, *, code: str) -> Path:
    resolved = path.resolve()
    authority_root = root.resolve()
    try:
        resolved.relative_to(authority_root)
    except ValueError as exc:
        raise EvidenceLaneError(
            code,
            "MEMORY.md discovery cannot escape its configured authority root.",
            status="MISMATCH",
            details={"filename": path.name, "root_name": root.name},
        ) from exc
    return resolved


def _directory_chain(project_root: Path, cwd: Path) -> list[Path]:
    root = project_root.resolve()
    current = _contained(
        cwd,
        root,
        code="CONVERSATION_MEMORY_WORKTREE_MISMATCH",
    )
    require(
        current.is_dir(),
        "CONVERSATION_MEMORY_CWD_INVALID",
        "The MEMORY.md working directory must exist and be a directory.",
        status="MISMATCH",
    )
    result = [root]
    cursor = root
    for part in current.relative_to(root).parts:
        cursor = cursor / part
        result.append(cursor)
    return result


def _configuration(codex_home: Path) -> dict[str, Any]:
    config_path = codex_home / "config.toml"
    if not config_path.exists():
        body: dict[str, Any] = {
            "fallback_filenames": [],
            "max_bytes": DEFAULT_CONVERSATION_MEMORY_MAX_BYTES,
            "config_present": False,
            "config_sha256": None,
        }
        body["config_contract_sha256"] = sha256_bytes(canonical_json_bytes(body))
        return body
    _contained(
        config_path,
        codex_home,
        code="CONVERSATION_MEMORY_CODEX_CONFIG_ESCAPE",
    )
    try:
        raw = config_path.read_bytes()
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise EvidenceLaneError(
            "CONVERSATION_MEMORY_CODEX_CONFIG_MALFORMED",
            "The active Codex config.toml cannot be parsed for MEMORY.md settings.",
            status="BLOCKED",
        ) from exc
    fallbacks_value = parsed.get("conversation_memory_doc_fallback_filenames", [])
    max_bytes_value = parsed.get(
        "conversation_memory_doc_max_bytes",
        DEFAULT_CONVERSATION_MEMORY_MAX_BYTES,
    )
    require(
        isinstance(fallbacks_value, list),
        "CONVERSATION_MEMORY_FALLBACKS_INVALID",
        "conversation_memory_doc_fallback_filenames must be an ordered list.",
        status="BLOCKED",
    )
    fallbacks: list[str] = []
    for value in fallbacks_value:
        name = str(value or "").strip()
        require(
            bool(_SAFE_FALLBACK_RE.fullmatch(name))
            and name not in _PRIMARY_FILENAMES
            and name not in fallbacks,
            "CONVERSATION_MEMORY_FALLBACK_INVALID",
            "Each MEMORY.md fallback must be one unique safe basename.",
            status="BLOCKED",
            fallback_name=name,
        )
        fallbacks.append(name)
    require(
        isinstance(max_bytes_value, int)
        and not isinstance(max_bytes_value, bool)
        and 1 <= max_bytes_value <= 1024 * 1024,
        "CONVERSATION_MEMORY_MAX_BYTES_INVALID",
        "conversation_memory_doc_max_bytes must be from 1 byte through 1 MiB.",
        status="BLOCKED",
    )
    body = {
        "fallback_filenames": fallbacks,
        "max_bytes": max_bytes_value,
        "config_present": True,
        "config_sha256": sha256_bytes(raw),
    }
    body["config_contract_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def _read_nonempty(
    path: Path, *, root: Path, max_bytes: int
) -> tuple[bytes, str] | None:
    resolved = _contained(
        path,
        root,
        code="CONVERSATION_MEMORY_PATH_ESCAPE",
    )
    require(
        resolved.is_file(),
        "CONVERSATION_MEMORY_SOURCE_NOT_FILE",
        "A MEMORY.md discovery candidate must be a regular file.",
        status="MISMATCH",
        filename=path.name,
    )
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise EvidenceLaneError(
            "CONVERSATION_MEMORY_SOURCE_UNREADABLE",
            "An applicable MEMORY.md source could not be read.",
            status="BLOCKED",
            details={"filename": path.name},
        ) from exc
    require(
        len(raw) <= max_bytes,
        "CONVERSATION_MEMORY_SOURCE_OVERSIZED",
        "An applicable MEMORY.md source exceeds the bounded memory-doc limit.",
        status="BLOCKED",
        filename=path.name,
        source_bytes=len(raw),
        memory_doc_max_bytes=max_bytes,
    )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceLaneError(
            "CONVERSATION_MEMORY_SOURCE_MALFORMED",
            "An applicable MEMORY.md source is not valid UTF-8.",
            status="BLOCKED",
            details={"filename": path.name},
        ) from exc
    require(
        "\x00" not in text,
        "CONVERSATION_MEMORY_SOURCE_MALFORMED",
        "An applicable MEMORY.md source contains a forbidden NUL byte.",
        status="BLOCKED",
        filename=path.name,
    )
    if not text.strip():
        return None
    return raw, text


@dataclass(frozen=True, slots=True)
class ResolvedConversationMemory:
    """Private continuation guidance plus its bounded public receipt."""

    guidance: str
    receipt: dict[str, Any]


def resolve_conversation_memory(
    *,
    codex_home: str | Path,
    project_root: str | Path,
    cwd: str | Path,
    project_id: str,
    governed_session_id: str,
    host_task_id: str,
    host_task_deep_link: str,
    host_session_id: str,
    workspace_id: str,
    active_plan_task_id: str,
    execution_profile: Mapping[str, Any],
) -> ResolvedConversationMemory:
    """Resolve global then project-root-to-CWD ``MEMORY.md`` guidance."""

    exact_project_id = _exact_text(project_id, field="project_id")
    exact_session_id = _exact_text(governed_session_id, field="governed_session_id")
    exact_task_id = _exact_text(host_task_id, field="host_task_id")
    exact_deep_link = _exact_text(
        host_task_deep_link,
        field="host_task_deep_link",
        max_length=1024,
    )
    exact_host_session_id = _exact_text(host_session_id, field="host_session_id")
    require(
        exact_task_id == exact_host_session_id
        and exact_deep_link == f"codex://threads/{exact_task_id}",
        "CONVERSATION_MEMORY_TASK_BINDING_MISMATCH",
        "MEMORY.md authority must bind the exact invoking Codex task and deep link.",
        status="MISMATCH",
    )
    exact_workspace_id = _exact_text(
        workspace_id,
        field="workspace_id",
        max_length=2048,
    )
    exact_plan_task_id = _exact_text(
        active_plan_task_id,
        field="active_plan_task_id",
    )
    profile = _bounded_execution_profile(execution_profile)
    home = Path(codex_home).resolve()
    root = Path(project_root).resolve()
    current = Path(cwd).resolve()
    require(
        home.is_dir() and root.is_dir(),
        "CONVERSATION_MEMORY_ROOT_INVALID",
        "Codex home and governed project root must exist before MEMORY.md discovery.",
        status="MISMATCH",
    )
    chain = _directory_chain(root, current)
    config = _configuration(home)
    max_bytes = int(config["max_bytes"])
    fallbacks = tuple(str(value) for value in config["fallback_filenames"])
    sources: list[dict[str, Any]] = []
    guidance_parts: list[str] = []
    total_bytes = 0

    def add_source(
        path: Path,
        *,
        scope: str,
        depth: int,
        locator: str,
        precedence_kind: str,
        containment_root: Path,
    ) -> bool:
        nonlocal total_bytes
        loaded = _read_nonempty(path, root=containment_root, max_bytes=max_bytes)
        if loaded is None:
            return False
        raw, text = loaded
        total_bytes += len(raw)
        require(
            total_bytes <= max_bytes,
            "CONVERSATION_MEMORY_CHAIN_OVERSIZED",
            "The merged MEMORY.md chain exceeds the bounded memory-doc limit.",
            status="BLOCKED",
            chain_bytes=total_bytes,
            memory_doc_max_bytes=max_bytes,
        )
        sources.append(
            {
                "scope": scope,
                "directory_depth": depth,
                "filename": path.name,
                "locator": locator,
                "precedence_kind": precedence_kind,
                "content_bytes": len(raw),
                "content_sha256": sha256_bytes(raw),
            }
        )
        guidance_parts.append(text)
        return True

    for rank, filename in enumerate(_PRIMARY_FILENAMES):
        candidate = home / filename
        if candidate.exists() and add_source(
            candidate,
            scope="GLOBAL",
            depth=-1,
            locator=f"CODEX_HOME/{filename}",
            precedence_kind="OVERRIDE" if rank == 0 else "STANDARD",
            containment_root=home,
        ):
            break

    candidates = (*_PRIMARY_FILENAMES, *fallbacks)
    for depth, directory in enumerate(chain):
        relative = directory.relative_to(root).as_posix()
        if relative == ".":
            relative = ""
        for rank, filename in enumerate(candidates):
            candidate = directory / filename
            if not candidate.exists():
                continue
            if add_source(
                candidate,
                scope="PROJECT",
                depth=depth,
                locator=(
                    f"PROJECT_ROOT/{relative}/{filename}"
                    if relative
                    else f"PROJECT_ROOT/{filename}"
                ),
                precedence_kind=(
                    "OVERRIDE"
                    if rank == 0
                    else "STANDARD"
                    if rank == 1
                    else "CONFIGURED_FALLBACK"
                ),
                containment_root=directory,
            ):
                break

    guidance = "\n\n".join(guidance_parts)
    chain_body = {
        "schema": CONVERSATION_MEMORY_CHAIN_SCHEMA,
        "merge_order": "GLOBAL_THEN_PROJECT_ROOT_TO_CWD",
        "nearer_guidance_overrides_earlier": True,
        "at_most_one_file_per_directory": True,
        "empty_files_skipped": True,
        "sources": sources,
    }
    source_chain_sha256 = sha256_bytes(canonical_json_bytes(chain_body))
    binding = {
        "project_id": exact_project_id,
        "governed_session_id": exact_session_id,
        "host_task_id": exact_task_id,
        "host_task_deep_link": exact_deep_link,
        "host_session_id": exact_host_session_id,
        "workspace_id_sha256": sha256_bytes(exact_workspace_id.encode("utf-8")),
        "project_root_sha256": sha256_bytes(
            os.path.normcase(str(root)).encode("utf-8")
        ),
        "cwd_relative": current.relative_to(root).as_posix(),
        "active_plan_task_id": exact_plan_task_id,
        "execution_profile": profile,
        "execution_profile_sha256": sha256_bytes(canonical_json_bytes(profile)),
    }
    binding_sha256 = sha256_bytes(canonical_json_bytes(binding))
    authority_body = {
        "schema": CONVERSATION_MEMORY_SCHEMA,
        "binding_sha256": binding_sha256,
        "source_chain_sha256": source_chain_sha256,
        "merged_guidance_sha256": sha256_bytes(guidance.encode("utf-8")),
        "memory_doc_config_sha256": config["config_contract_sha256"],
        "memory_doc_max_bytes": max_bytes,
        "source_count": len(sources),
        "total_guidance_bytes": total_bytes,
        "user_observed_host_evidence": {
            "artifact_sha256": USER_OBSERVED_OPTIMIZED_CONVERSATION_ARTIFACT_SHA256,
            "optimized_conversation_wording_observed": True,
            "causal_attribution": "UNVERIFIED_PENDING_INSTALLED_HOST_AB",
        },
        "authority_effects": {
            "bounded_continuation_guidance_only": True,
            "host_compaction_disabled": False,
            "host_wording_control_claimed": False,
            "task_merge_allowed": False,
            "permission_widening_allowed": False,
            "hil_inference_allowed": False,
            "pointer_movement_allowed": False,
            "project_memory_replaced": False,
            "private_reasoning_exposed": False,
            "hooks_required_for_explicit_public_actions": False,
        },
    }
    authority_sha256 = sha256_bytes(canonical_json_bytes(authority_body))
    receipt = {
        **authority_body,
        "status": "PASS",
        "resolution_scope": "ONCE_PER_EXACT_CODEX_RUN_SESSION_BINDING",
        "binding": binding,
        "source_chain": chain_body,
        "conversation_memory_authority_sha256": authority_sha256,
        "resolved_at": utc_now(),
        "raw_guidance_text_returned": False,
        "absolute_source_paths_returned": False,
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    return ResolvedConversationMemory(guidance=guidance, receipt=receipt)


class ConversationMemoryManager:
    """Cache one resolution per exact run/session/worktree/task binding."""

    def __init__(self) -> None:
        self._cache: dict[str, ResolvedConversationMemory] = {}

    def resolve(self, **kwargs: Any) -> ResolvedConversationMemory:
        cache_key_body = {
            key: (
                dict(value)
                if isinstance(value, Mapping)
                else os.path.normcase(str(Path(value).resolve()))
                if key in {"codex_home", "project_root", "cwd"}
                else value
            )
            for key, value in kwargs.items()
        }
        cache_key = sha256_bytes(canonical_json_bytes(cache_key_body))
        existing = self._cache.get(cache_key)
        if existing is not None:
            return existing
        resolved = resolve_conversation_memory(**kwargs)
        self._cache[cache_key] = resolved
        return resolved

"""First-class, project-isolated Codex ``AGENTS.md`` authority.

The resolver follows the public Codex discovery order while keeping instruction
text private to the local runtime.  Public plugin surfaces receive only an
attested, bounded source-chain receipt; the receipt cannot grant lifecycle,
Plan, HIL, Git, installation, or pointer authority.
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

AGENT_CONFIGURATION_SCHEMA = "evidence-lane.agent-configuration-authority.v1"
AGENT_CONFIGURATION_CHAIN_SCHEMA = "evidence-lane.agent-instruction-chain.v1"
DEFAULT_PROJECT_DOC_MAX_BYTES = 32 * 1024
OFFICIAL_AGENTS_MD_GUIDE_URL = (
    "https://learn.chatgpt.com/docs/agent-configuration/agents-md"
)
OFFICIAL_AGENTS_MD_GUIDE_SHA256 = (
    "9D1F87A2D1CB55B4782B95ABE710692B35B9659789C2DB31A22C7074A3383E8E"
)
OFFICIAL_HOOKS_GUIDE_URL = "https://learn.chatgpt.com/docs/hooks"
OFFICIAL_HOOKS_GUIDE_SHA256 = (
    "017D2A86BC8654FB5E566F968019E5BC23F65AB0BCA3B051B92EC74BC6DA130A"
)

_PRIMARY_FILENAMES = ("AGENTS.override.md", "AGENTS.md")
_SAFE_FALLBACK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _exact_text(value: object, *, field: str, max_length: int = 512) -> str:
    exact = str(value or "").strip()
    require(
        bool(exact) and len(exact) <= max_length,
        "AGENT_CONFIGURATION_BINDING_INVALID",
        "AGENTS.md resolution requires every exact runtime binding field.",
        status="MISMATCH",
        field=field,
    )
    return exact


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(
        str(right.resolve())
    )


def _require_contained(path: Path, root: Path, *, code: str) -> Path:
    resolved_path = path.resolve()
    resolved_root = root.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise EvidenceLaneError(
            code,
            "AGENTS.md discovery cannot escape its exact configured authority root.",
            status="MISMATCH",
            details={
                "path_name": path.name,
                "root_name": root.name,
            },
        ) from exc
    return resolved_path


def _read_nonempty_instruction_file(
    path: Path,
    *,
    containment_root: Path,
    max_bytes: int,
) -> tuple[bytes, str] | None:
    resolved = _require_contained(
        path,
        containment_root,
        code="AGENT_CONFIGURATION_PATH_ESCAPE",
    )
    require(
        resolved.is_file(),
        "AGENT_CONFIGURATION_SOURCE_NOT_FILE",
        "An AGENTS.md discovery candidate must be a regular file.",
        status="MISMATCH",
        path_name=path.name,
    )
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise EvidenceLaneError(
            "AGENT_CONFIGURATION_SOURCE_UNREADABLE",
            "An applicable AGENTS.md source could not be read.",
            status="BLOCKED",
            details={"path_name": path.name},
        ) from exc
    require(
        len(raw) <= max_bytes,
        "AGENT_CONFIGURATION_SOURCE_OVERSIZED",
        "An applicable AGENTS.md source exceeds project_doc_max_bytes.",
        status="BLOCKED",
        path_name=path.name,
        source_bytes=len(raw),
        project_doc_max_bytes=max_bytes,
    )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EvidenceLaneError(
            "AGENT_CONFIGURATION_SOURCE_MALFORMED",
            "An applicable AGENTS.md source is not valid UTF-8.",
            status="BLOCKED",
            details={"path_name": path.name},
        ) from exc
    require(
        "\x00" not in text,
        "AGENT_CONFIGURATION_SOURCE_MALFORMED",
        "An applicable AGENTS.md source contains a forbidden NUL byte.",
        status="BLOCKED",
        path_name=path.name,
    )
    if not text.strip():
        return None
    return raw, text


def _load_codex_project_doc_config(codex_home: Path) -> dict[str, Any]:
    config_path = codex_home / "config.toml"
    if not config_path.exists():
        body = {
            "fallback_filenames": [],
            "project_doc_max_bytes": DEFAULT_PROJECT_DOC_MAX_BYTES,
            "config_present": False,
            "config_sha256": None,
        }
        body["config_contract_sha256"] = sha256_bytes(canonical_json_bytes(body))
        return body
    _require_contained(
        config_path,
        codex_home,
        code="AGENT_CONFIGURATION_CODEX_CONFIG_ESCAPE",
    )
    try:
        raw = config_path.read_bytes()
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise EvidenceLaneError(
            "AGENT_CONFIGURATION_CODEX_CONFIG_MALFORMED",
            "The active Codex config.toml cannot be parsed for AGENTS.md settings.",
            status="BLOCKED",
        ) from exc
    raw_fallbacks = parsed.get("project_doc_fallback_filenames", [])
    raw_max_bytes = parsed.get(
        "project_doc_max_bytes", DEFAULT_PROJECT_DOC_MAX_BYTES
    )
    require(
        isinstance(raw_fallbacks, list),
        "AGENT_CONFIGURATION_FALLBACKS_INVALID",
        "project_doc_fallback_filenames must be an ordered list.",
        status="BLOCKED",
    )
    fallback_filenames: list[str] = []
    for value in raw_fallbacks:
        name = str(value or "").strip()
        require(
            bool(_SAFE_FALLBACK_RE.fullmatch(name))
            and name not in _PRIMARY_FILENAMES
            and name not in fallback_filenames,
            "AGENT_CONFIGURATION_FALLBACK_INVALID",
            "Every configured AGENTS.md fallback must be one unique safe basename.",
            status="BLOCKED",
            fallback_name=name,
        )
        fallback_filenames.append(name)
    require(
        isinstance(raw_max_bytes, int)
        and not isinstance(raw_max_bytes, bool)
        and 1 <= raw_max_bytes <= 1024 * 1024,
        "AGENT_CONFIGURATION_MAX_BYTES_INVALID",
        "project_doc_max_bytes must be an integer from 1 byte through 1 MiB.",
        status="BLOCKED",
    )
    body = {
        "fallback_filenames": fallback_filenames,
        "project_doc_max_bytes": raw_max_bytes,
        "config_present": True,
        "config_sha256": sha256_bytes(raw),
    }
    body["config_contract_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def _directory_chain(project_root: Path, cwd: Path) -> list[Path]:
    root = project_root.resolve()
    current = _require_contained(
        cwd,
        root,
        code="AGENT_CONFIGURATION_WORKTREE_MISMATCH",
    )
    require(
        current.is_dir(),
        "AGENT_CONFIGURATION_CWD_INVALID",
        "The AGENTS.md working directory must exist and be a directory.",
        status="MISMATCH",
    )
    relative = current.relative_to(root)
    result = [root]
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        result.append(cursor)
    return result


@dataclass(frozen=True, slots=True)
class ResolvedAgentConfiguration:
    """Private merged instructions paired with their bounded public receipt."""

    instructions: str
    receipt: dict[str, Any]


def resolve_agent_configuration(
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
) -> ResolvedAgentConfiguration:
    """Resolve the exact global plus root-to-cwd instruction chain."""

    exact_project_id = _exact_text(project_id, field="project_id")
    exact_session_id = _exact_text(
        governed_session_id, field="governed_session_id"
    )
    exact_task_id = _exact_text(host_task_id, field="host_task_id")
    exact_deep_link = _exact_text(
        host_task_deep_link, field="host_task_deep_link", max_length=1024
    )
    exact_host_session_id = _exact_text(
        host_session_id, field="host_session_id"
    )
    require(
        exact_task_id == exact_host_session_id
        and exact_deep_link == f"codex://threads/{exact_task_id}",
        "AGENT_CONFIGURATION_TASK_BINDING_MISMATCH",
        "The AGENTS.md authority must bind the exact invoking Codex task and deep link.",
        status="MISMATCH",
    )
    exact_workspace_id = _exact_text(workspace_id, field="workspace_id", max_length=2048)
    exact_plan_task_id = _exact_text(
        active_plan_task_id, field="active_plan_task_id"
    )
    profile = {
        field: _exact_text(execution_profile.get(field), field=field)
        for field in ("model", "submodel", "reasoning_effort", "reasoning_speed")
    }

    exact_codex_home = Path(codex_home).resolve()
    exact_project_root = Path(project_root).resolve()
    exact_cwd = Path(cwd).resolve()
    require(
        exact_project_root.is_dir(),
        "AGENT_CONFIGURATION_ROOT_INVALID",
        "The governed project root must exist before AGENTS.md discovery.",
        status="MISMATCH",
    )
    require(
        not exact_codex_home.exists() or exact_codex_home.is_dir(),
        "AGENT_CONFIGURATION_CODEX_HOME_INVALID",
        "Codex home must be a directory when that optional global root exists.",
        status="MISMATCH",
    )
    directory_chain = _directory_chain(exact_project_root, exact_cwd)
    config = _load_codex_project_doc_config(exact_codex_home)
    max_bytes = int(config["project_doc_max_bytes"])
    fallbacks = tuple(str(value) for value in config["fallback_filenames"])

    sources: list[dict[str, Any]] = []
    instruction_parts: list[str] = []
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
        loaded = _read_nonempty_instruction_file(
            path,
            containment_root=containment_root,
            max_bytes=max_bytes,
        )
        if loaded is None:
            return False
        raw, text = loaded
        total_bytes += len(raw)
        require(
            total_bytes <= max_bytes,
            "AGENT_CONFIGURATION_CHAIN_OVERSIZED",
            "The merged AGENTS.md chain exceeds project_doc_max_bytes.",
            status="BLOCKED",
            chain_bytes=total_bytes,
            project_doc_max_bytes=max_bytes,
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
        instruction_parts.append(text)
        return True

    for rank, filename in enumerate(_PRIMARY_FILENAMES):
        path = exact_codex_home / filename
        if path.exists() and add_source(
            path,
            scope="GLOBAL",
            depth=-1,
            locator=f"CODEX_HOME/{filename}",
            precedence_kind=("OVERRIDE" if rank == 0 else "STANDARD"),
            containment_root=exact_codex_home,
        ):
            break

    candidates = (*_PRIMARY_FILENAMES, *fallbacks)
    for depth, directory in enumerate(directory_chain):
        relative_dir = directory.relative_to(exact_project_root).as_posix()
        if relative_dir == ".":
            relative_dir = ""
        for rank, filename in enumerate(candidates):
            path = directory / filename
            if not path.exists():
                continue
            if add_source(
                path,
                scope="PROJECT",
                depth=depth,
                locator=(
                    f"PROJECT_ROOT/{relative_dir}/{filename}"
                    if relative_dir
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

    merged = "\n\n".join(instruction_parts)
    chain_body = {
        "schema": AGENT_CONFIGURATION_CHAIN_SCHEMA,
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
            os.path.normcase(str(exact_project_root)).encode("utf-8")
        ),
        "cwd_relative": exact_cwd.relative_to(exact_project_root).as_posix(),
        "active_plan_task_id": exact_plan_task_id,
        "execution_profile": profile,
        "execution_profile_sha256": sha256_bytes(canonical_json_bytes(profile)),
    }
    binding_sha256 = sha256_bytes(canonical_json_bytes(binding))
    authority_body = {
        "schema": AGENT_CONFIGURATION_SCHEMA,
        "binding_sha256": binding_sha256,
        "source_chain_sha256": source_chain_sha256,
        "merged_instruction_sha256": sha256_bytes(merged.encode("utf-8")),
        "project_doc_config_sha256": config["config_contract_sha256"],
        "project_doc_max_bytes": max_bytes,
        "source_count": len(sources),
        "total_instruction_bytes": total_bytes,
        "official_contracts": {
            "agents_md": {
                "url": OFFICIAL_AGENTS_MD_GUIDE_URL,
                "content_sha256": OFFICIAL_AGENTS_MD_GUIDE_SHA256,
            },
            "hooks": {
                "url": OFFICIAL_HOOKS_GUIDE_URL,
                "content_sha256": OFFICIAL_HOOKS_GUIDE_SHA256,
            },
        },
        "authority_effects": {
            "instruction_context_only": True,
            "task_merge_allowed": False,
            "permission_widening_allowed": False,
            "hil_inference_allowed": False,
            "pointer_movement_allowed": False,
            "private_reasoning_exposed": False,
            "hooks_required_for_public_actions": False,
        },
    }
    authority_sha256 = sha256_bytes(canonical_json_bytes(authority_body))
    receipt = {
        **authority_body,
        "status": "PASS",
        "resolution_scope": "ONCE_PER_EXACT_CODEX_RUN_SESSION_BINDING",
        "binding": binding,
        "source_chain": chain_body,
        "agent_configuration_authority_sha256": authority_sha256,
        "resolved_at": utc_now(),
        "raw_instruction_text_returned": False,
        "absolute_source_paths_returned": False,
    }
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    return ResolvedAgentConfiguration(instructions=merged, receipt=receipt)


class AgentConfigurationManager:
    """Cache one resolution per exact run/session/worktree/task binding."""

    def __init__(self) -> None:
        self._cache: dict[str, ResolvedAgentConfiguration] = {}

    def resolve(self, **kwargs: Any) -> ResolvedAgentConfiguration:
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
        resolved = resolve_agent_configuration(**kwargs)
        self._cache[cache_key] = resolved
        return resolved

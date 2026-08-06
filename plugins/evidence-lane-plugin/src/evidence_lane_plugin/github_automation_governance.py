"""Fail-closed GitHub Actions, gh-aw, and MCP exposure governance."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes, sha256_file
from .redaction import redact_text

GITHUB_AW_GUARD_ORDER = (
    "TOOL_ALLOWED",
    "REPOSITORY_ALLOWED",
    "ROLE_ALLOWED",
    "PRIVATE_REPOSITORY_ALLOWED",
    "ACTOR_NOT_BLOCKED",
    "CONTENT_INTEGRITY_SUFFICIENT",
)

CONTENT_INTEGRITY_LEVELS = ("none", "unapproved", "approved", "merged")

_DENY_CODES = {
    "TOOL_ALLOWED": -32001,
    "REPOSITORY_ALLOWED": -32002,
    "ROLE_ALLOWED": -32003,
    "PRIVATE_REPOSITORY_ALLOWED": -32004,
    "ACTOR_NOT_BLOCKED": -32005,
    "CONTENT_INTEGRITY_SUFFICIENT": -32006,
}
_TOOL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_REPOSITORY = re.compile(r"(?:\*|[A-Za-z0-9_.-]+)/(?:\*|[A-Za-z0-9_.-]+)")
_ROLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}")
_REMOTE_ACTION = re.compile(
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    r"(?:/[A-Za-z0-9_.-]+)*@(?P<sha>[0-9A-Fa-f]{40})"
)
_DOCKER_DIGEST_ACTION = re.compile(r"docker://[^\s@]+@sha256:[0-9A-Fa-f]{64}")
_USES_LINE = re.compile(r"^\s*(?:-\s*)?uses\s*:\s*(?P<value>.*?)\s*$")
_EXECUTION_EVIDENCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_OUTPUT_THREAT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "PROMPT_INJECTION_MARKER",
        re.compile(
            r"(?i)\bignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior)\s+"
            r"(?:instructions?|prompts?)\b"
        ),
    ),
    (
        "DESTRUCTIVE_GIT_MARKER",
        re.compile(r"(?i)\bgit\s+reset\s+--hard\b"),
    ),
    (
        "DESTRUCTIVE_FILESYSTEM_MARKER",
        re.compile(r"(?i)(?:\brm\s+-rf\s+/(?:\s|$)|\bRemove-Item\b[^\n]*\s-Recurse\b)"),
    ),
    (
        "PIPE_TO_SHELL_MARKER",
        re.compile(r"(?i)\b(?:curl|wget)\b[^\n|]{0,500}\|\s*(?:ba)?sh\b"),
    ),
    (
        "POWERSHELL_EXPRESSION_MARKER",
        re.compile(r"(?i)\b(?:Invoke-Expression|iex)\b"),
    ),
)
_DEFAULT_SAFE_OUTPUT_CHARS = 4_000


def _normalize_execution_ids(
    values: Sequence[str | int], *, field: str
) -> tuple[str, ...]:
    require(
        not isinstance(values, (str, bytes)),
        "GITHUB_EXECUTION_EVIDENCE_INVALID",
        f"{field} must be an array of exact identifiers.",
        status="BLOCKED",
        field=field,
    )
    normalized = tuple(sorted({str(value).strip() for value in values}))
    for value in normalized:
        require(
            bool(_EXECUTION_EVIDENCE_ID.fullmatch(value)),
            "GITHUB_EXECUTION_EVIDENCE_INVALID",
            f"{field} contains an invalid identifier.",
            status="BLOCKED",
            field=field,
            value=value,
        )
    return normalized


def classify_github_execution_evidence(
    *,
    actions_run_ids: Sequence[str | int],
    agent_session_ids: Sequence[str | int],
) -> dict[str, Any]:
    """Keep GitHub Actions runs distinct from Copilot agent sessions."""

    actions = _normalize_execution_ids(actions_run_ids, field="actions_run_ids")
    sessions = _normalize_execution_ids(agent_session_ids, field="agent_session_ids")
    if sessions:
        surface = "AGENT_SESSION_OBSERVED"
    elif actions:
        surface = "ACTIONS_ONLY"
    else:
        surface = "NO_EXECUTION_EVIDENCE"
    body: dict[str, Any] = {
        "schema": "evidence-lane.github-execution-evidence.v1",
        "status": "PASS",
        "execution_surface": surface,
        "actions_run_ids": list(actions),
        "agent_session_ids": list(sessions),
        "actions_run_count": len(actions),
        "agent_session_count": len(sessions),
        "agent_session_proven": bool(sessions),
        "actions_are_agent_sessions": False,
    }
    body["receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def _escape_untrusted_control_characters(value: str) -> str:
    return "".join(
        character
        if character in {"\n", "\t"} or ord(character) >= 32 and ord(character) != 127
        else f"\\x{ord(character):02X}"
        for character in value
    )


def inspect_agent_output(
    raw_output: str,
    *,
    infrastructure_error: str | None = None,
    max_safe_chars: int = _DEFAULT_SAFE_OUTPUT_CHARS,
) -> dict[str, Any]:
    """Create an advisory, secret-free receipt for untrusted execution output.

    This inspection happens after output exists. It never claims to prevent or
    roll back an action that already ran.
    """

    require(
        isinstance(raw_output, str),
        "AGENT_OUTPUT_INVALID",
        "Agent output must be text.",
        status="BLOCKED",
    )
    require(
        isinstance(max_safe_chars, int) and 1 <= max_safe_chars <= 64_000,
        "AGENT_OUTPUT_LIMIT_INVALID",
        "The safe-output character limit must be between 1 and 64000.",
        status="BLOCKED",
    )
    findings = [
        {"code": code, "severity": "HIGH"}
        for code, pattern in _OUTPUT_THREAT_RULES
        if pattern.search(raw_output)
    ]
    safe_output = _escape_untrusted_control_characters(
        redact_text(raw_output[-max_safe_chars:])
    )
    safe_error = None
    if infrastructure_error:
        safe_error = _escape_untrusted_control_characters(
            redact_text(str(infrastructure_error)[-max_safe_chars:])
        )
    body: dict[str, Any] = {
        "schema": "evidence-lane.agent-output-inspection.v1",
        "status": "ALERT" if findings or safe_error else "PASS",
        "advisory_only": True,
        "post_execution": True,
        "threat_status": "ALERT" if findings else "PASS",
        "infrastructure_status": "ERROR" if safe_error else "PASS",
        "findings": findings,
        "raw_output_sha256": sha256_bytes(raw_output.encode("utf-8")),
        "raw_output_chars": len(raw_output),
        "threat_scan_chars": len(raw_output),
        "threat_scan_complete": True,
        "output_truncated": len(raw_output) > max_safe_chars,
        "safe_output": safe_output,
        "safe_output_sha256": sha256_bytes(safe_output.encode("utf-8")),
        "safe_infrastructure_error": safe_error,
    }
    body["receipt_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def _normalize_string_sequence(
    value: object,
    *,
    field: str,
    pattern: re.Pattern[str],
    allow_empty: bool = False,
    lowercase: bool = False,
) -> tuple[str, ...]:
    require(
        isinstance(value, (list, tuple)),
        "GITHUB_AW_POLICY_INVALID",
        f"{field} must be a JSON array of exact strings.",
        status="BLOCKED",
        field=field,
    )
    sequence = cast(list[object] | tuple[object, ...], value)
    normalized: list[str] = []
    for raw in sequence:
        item = str(raw).strip()
        if lowercase:
            item = item.lower()
        require(
            bool(pattern.fullmatch(item)),
            "GITHUB_AW_POLICY_INVALID",
            f"{field} contains an invalid value.",
            status="BLOCKED",
            field=field,
            value=item,
        )
        normalized.append(item)
    unique = tuple(sorted(set(normalized)))
    require(
        allow_empty or bool(unique),
        "GITHUB_AW_POLICY_INVALID",
        f"{field} cannot be empty because an empty authority list is ambiguous.",
        status="BLOCKED",
        field=field,
    )
    return unique


@dataclass(frozen=True, slots=True)
class GitHubAWPolicy:
    """Exact internal authorization policy applied before any GitHub API call."""

    allowed_tools: tuple[str, ...]
    allowed_repositories: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    allow_private_repositories: bool
    blocked_users: tuple[str, ...]
    minimum_content_integrity: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> GitHubAWPolicy:
        expected = {
            "allowed_tools",
            "allowed_repositories",
            "allowed_roles",
            "allow_private_repositories",
            "blocked_users",
            "minimum_content_integrity",
        }
        unknown = sorted(set(raw) - expected)
        missing = sorted(expected - set(raw))
        require(
            not unknown and not missing,
            "GITHUB_AW_POLICY_INVALID",
            "The gh-aw policy must contain exactly the governed fields.",
            status="BLOCKED",
            unknown_fields=unknown,
            missing_fields=missing,
        )
        private_allowed = raw["allow_private_repositories"]
        require(
            isinstance(private_allowed, bool),
            "GITHUB_AW_POLICY_INVALID",
            "allow_private_repositories must be a JSON boolean.",
            status="BLOCKED",
            field="allow_private_repositories",
        )
        integrity = str(raw["minimum_content_integrity"]).strip().lower()
        require(
            integrity in CONTENT_INTEGRITY_LEVELS,
            "GITHUB_AW_POLICY_INVALID",
            "minimum_content_integrity is not a recognized ordered level.",
            status="BLOCKED",
            value=integrity,
        )
        return cls(
            allowed_tools=_normalize_string_sequence(
                raw["allowed_tools"], field="allowed_tools", pattern=_TOOL_NAME
            ),
            allowed_repositories=_normalize_string_sequence(
                raw["allowed_repositories"],
                field="allowed_repositories",
                pattern=_REPOSITORY,
                lowercase=True,
            ),
            allowed_roles=_normalize_string_sequence(
                raw["allowed_roles"],
                field="allowed_roles",
                pattern=_ROLE,
                lowercase=True,
            ),
            allow_private_repositories=cast(bool, private_allowed),
            blocked_users=_normalize_string_sequence(
                raw["blocked_users"],
                field="blocked_users",
                pattern=_ROLE,
                allow_empty=True,
                lowercase=True,
            ),
            minimum_content_integrity=integrity,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed_tools": list(self.allowed_tools),
            "allowed_repositories": list(self.allowed_repositories),
            "allowed_roles": list(self.allowed_roles),
            "allow_private_repositories": self.allow_private_repositories,
            "blocked_users": list(self.blocked_users),
            "minimum_content_integrity": self.minimum_content_integrity,
        }

    @property
    def sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.as_dict()))


@dataclass(frozen=True, slots=True)
class GitHubAWRequest:
    """Non-secret facts required by the ordered gh-aw authorization guards."""

    tool_name: str
    repository: str
    actor: str
    role: str
    repository_private: bool
    content_integrity: str

    def normalized(self) -> GitHubAWRequest:
        tool = self.tool_name.strip()
        repository = self.repository.strip().lower()
        actor = self.actor.strip().lower()
        role = self.role.strip().lower()
        integrity = self.content_integrity.strip().lower()
        require(
            bool(_TOOL_NAME.fullmatch(tool)),
            "GITHUB_AW_REQUEST_INVALID",
            "The requested MCP tool name is invalid.",
            status="BLOCKED",
        )
        require(
            bool(_REPOSITORY.fullmatch(repository)) and "*" not in repository,
            "GITHUB_AW_REQUEST_INVALID",
            "The requested repository must be an exact owner/repository name.",
            status="BLOCKED",
        )
        require(
            bool(_ROLE.fullmatch(actor)) and bool(_ROLE.fullmatch(role)),
            "GITHUB_AW_REQUEST_INVALID",
            "Actor and role must be exact non-secret identifiers.",
            status="BLOCKED",
        )
        require(
            integrity in CONTENT_INTEGRITY_LEVELS,
            "GITHUB_AW_REQUEST_INVALID",
            "The request content-integrity level is unknown.",
            status="BLOCKED",
        )
        return GitHubAWRequest(
            tool_name=tool,
            repository=repository,
            actor=actor,
            role=role,
            repository_private=bool(self.repository_private),
            content_integrity=integrity,
        )


def _repository_allowed(repository: str, patterns: Sequence[str]) -> bool:
    owner, name = repository.split("/", 1)
    for pattern in patterns:
        allowed_owner, allowed_name = pattern.split("/", 1)
        if allowed_owner in {"*", owner} and allowed_name in {"*", name}:
            return True
    return False


def evaluate_github_aw_access(
    policy: GitHubAWPolicy,
    request: GitHubAWRequest,
) -> dict[str, Any]:
    """Evaluate the canonical six guards and stop at the first failure."""

    exact = request.normalized()
    request_integrity = CONTENT_INTEGRITY_LEVELS.index(exact.content_integrity)
    minimum_integrity = CONTENT_INTEGRITY_LEVELS.index(policy.minimum_content_integrity)
    checks = (
        ("TOOL_ALLOWED", exact.tool_name in policy.allowed_tools),
        (
            "REPOSITORY_ALLOWED",
            _repository_allowed(exact.repository, policy.allowed_repositories),
        ),
        ("ROLE_ALLOWED", exact.role in policy.allowed_roles),
        (
            "PRIVATE_REPOSITORY_ALLOWED",
            not exact.repository_private or policy.allow_private_repositories,
        ),
        ("ACTOR_NOT_BLOCKED", exact.actor not in policy.blocked_users),
        (
            "CONTENT_INTEGRITY_SUFFICIENT",
            request_integrity >= minimum_integrity,
        ),
    )
    trace: list[dict[str, Any]] = []
    for guard, passed in checks:
        trace.append({"guard": guard, "passed": passed})
        if not passed:
            return {
                "schema": "evidence-lane.github-aw-access-decision.v1",
                "status": "BLOCKED",
                "decision": "DENY",
                "code": _DENY_CODES[guard],
                "failed_guard": guard,
                "guard_order": list(GITHUB_AW_GUARD_ORDER),
                "guard_trace": trace,
                "policy_sha256": policy.sha256,
            }
    return {
        "schema": "evidence-lane.github-aw-access-decision.v1",
        "status": "PASS",
        "decision": "ALLOW",
        "code": 0,
        "failed_guard": None,
        "guard_order": list(GITHUB_AW_GUARD_ORDER),
        "guard_trace": trace,
        "policy_sha256": policy.sha256,
    }


def parse_mcp_tool_allowlist(
    value: str | Sequence[str] | None,
) -> tuple[str, ...] | None:
    """Parse an explicit exact-name allowlist; absence preserves local inventory."""

    if value is None:
        return None
    raw: object = value
    if isinstance(value, str):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            require(
                False,
                "MCP_TOOL_POLICY_INVALID",
                "EVIDENCE_LANE_MCP_ALLOWED_TOOLS must be a JSON array.",
                status="BLOCKED",
                error=str(exc),
            )
    require(
        isinstance(raw, (list, tuple)),
        "MCP_TOOL_POLICY_INVALID",
        "The MCP exposure policy must be a JSON array of exact tool names.",
        status="BLOCKED",
    )
    sequence = cast(list[object] | tuple[object, ...], raw)
    normalized = tuple(sorted({str(item).strip() for item in sequence}))
    for item in normalized:
        require(
            bool(_TOOL_NAME.fullmatch(item)),
            "MCP_TOOL_POLICY_INVALID",
            "The MCP exposure policy contains an invalid exact tool name.",
            status="BLOCKED",
            value=item,
        )
    return normalized


def apply_fastmcp_tool_filter(
    server: Any,
    allowed_tools: str | Sequence[str] | None,
) -> dict[str, Any]:
    """Remove unlisted registered tools using the pinned FastMCP 1.28.1 surface."""

    manager = getattr(server, "_tool_manager", None)
    require(
        manager is not None
        and callable(getattr(manager, "list_tools", None))
        and callable(getattr(server, "remove_tool", None)),
        "MCP_TOOL_FILTER_UNSUPPORTED",
        "The pinned FastMCP tool-manager surface is unavailable.",
        status="BLOCKED",
    )
    exact_manager = cast(Any, manager)
    registered = tuple(sorted(tool.name for tool in exact_manager.list_tools()))
    explicit = parse_mcp_tool_allowlist(allowed_tools)
    if explicit is None:
        exposed = registered
        removed: tuple[str, ...] = ()
        mode = "LOCAL_REGISTERED_INVENTORY"
    else:
        unknown = sorted(set(explicit) - set(registered))
        require(
            not unknown,
            "MCP_TOOL_POLICY_UNKNOWN_TOOL",
            "The MCP exposure policy names tools that are not registered.",
            status="BLOCKED",
            unknown_tools=unknown,
        )
        exposed = tuple(name for name in registered if name in set(explicit))
        removed = tuple(name for name in registered if name not in set(explicit))
        for name in removed:
            server.remove_tool(name)
        mode = "EXACT_ALLOWLIST"
    policy = {
        "schema": "evidence-lane.mcp-tool-exposure-policy.v1",
        "mode": mode,
        "registered_tools": list(registered),
        "exposed_tools": list(exposed),
        "removed_tools": list(removed),
    }
    receipt = dict(policy)
    receipt["policy_sha256"] = sha256_bytes(canonical_json_bytes(policy))
    receipt["receipt_sha256"] = sha256_bytes(canonical_json_bytes(receipt))
    return receipt


def _parse_uses_value(raw: str) -> str | None:
    value = raw.strip()
    if not value:
        return None
    if value[0] in {'"', "'"}:
        quote = value[0]
        end = value.find(quote, 1)
        if end < 0:
            return None
        trailing = value[end + 1 :].strip()
        if trailing and not trailing.startswith("#"):
            return None
        return value[1:end]
    return value.split("#", 1)[0].strip().split(maxsplit=1)[0]


def validate_action_reference(reference: str) -> dict[str, Any]:
    """Classify one workflow ``uses`` reference under immutable-pin rules."""

    exact = reference.strip()
    if exact.startswith("./"):
        result = {"status": "PASS", "kind": "LOCAL_SAME_COMMIT"}
    elif _REMOTE_ACTION.fullmatch(exact):
        result = {"status": "PASS", "kind": "REMOTE_FULL_COMMIT_SHA"}
    elif _DOCKER_DIGEST_ACTION.fullmatch(exact):
        result = {"status": "PASS", "kind": "DOCKER_SHA256_DIGEST"}
    else:
        result = {
            "status": "BLOCKED",
            "kind": "UNPINNED_OR_DYNAMIC",
            "code": "WORKFLOW_ACTION_REF_NOT_IMMUTABLE",
        }
    return {"reference": exact, **result}


def audit_workflow_action_pins(
    workflow_root: str | Path,
    *,
    include_glob: str = "*.yml",
) -> dict[str, Any]:
    """Audit every selected workflow without retaining external absolute paths."""

    root = Path(workflow_root).resolve()
    require(
        root.is_dir(),
        "WORKFLOW_ROOT_MISSING",
        "The workflow audit root does not exist.",
        status="BLOCKED",
    )
    paths = tuple(sorted(path for path in root.rglob(include_glob) if path.is_file()))
    require(
        bool(paths),
        "WORKFLOW_SET_EMPTY",
        "No workflow files matched the governed audit selection.",
        status="BLOCKED",
        include_glob=include_glob,
    )
    references: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    kind_counts: dict[str, int] = {}
    for path in paths:
        relative = path.relative_to(root).as_posix()
        file_reference_count = 0
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8", errors="strict").splitlines(),
            start=1,
        ):
            match = _USES_LINE.match(line)
            if match is None:
                continue
            file_reference_count += 1
            reference = _parse_uses_value(match.group("value"))
            if reference is None:
                finding = {
                    "path": relative,
                    "line": line_number,
                    "reference": "UNPARSEABLE",
                    "status": "BLOCKED",
                    "kind": "UNPARSEABLE",
                    "code": "WORKFLOW_ACTION_REF_UNPARSEABLE",
                }
            else:
                finding = {
                    "path": relative,
                    "line": line_number,
                    **validate_action_reference(reference),
                }
            references.append(finding)
            kind = str(finding["kind"])
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            if finding["status"] != "PASS":
                violations.append(finding)
        files.append(
            {
                "path": relative,
                "sha256": sha256_file(path),
                "reference_count": file_reference_count,
            }
        )
    reference_set_sha256 = sha256_bytes(canonical_json_bytes(references))
    body = {
        "schema": "evidence-lane.workflow-action-pin-audit.v1",
        "status": "PASS" if not violations else "BLOCKED",
        "include_glob": include_glob,
        "file_count": len(files),
        "reference_count": len(references),
        "kind_counts": dict(sorted(kind_counts.items())),
        "violation_count": len(violations),
        "violations": violations,
        "files": files,
        "reference_set_sha256": reference_set_sha256,
    }
    body["audit_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body

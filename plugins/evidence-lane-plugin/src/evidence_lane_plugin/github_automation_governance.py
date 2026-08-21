"""Fail-closed GitHub Actions, gh-aw, and MCP exposure governance."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
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
_USING_LINE = re.compile(r"^\s*using\s*:\s*(?P<value>.*?)\s*$")
_ACTION_RUNTIME_SHA256 = re.compile(r"[A-Fa-f0-9]{64}")
_ACTION_RUNTIME_ALLOWED = {"composite", "docker", "node24"}
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
GH_AW_HARNESS_REFERENCE_COMMIT = "75ed171c12321e0cf4249a732c9860386bdc46a0"
_HARNESS_OUTCOMES = ("PASS", "FAIL", "ERROR")
_HARNESS_FORBIDDEN_INPUT_KEYS = {
    "cmd",
    "command",
    "cwd",
    "executable",
    "script",
    "shell",
    "working_directory",
}


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


def _normalize_harness_payload(value: object, *, path: str = "payload") -> Any:
    """Accept only bounded JSON fixtures and reject command-shaped inputs."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            require(
                isinstance(raw_key, str) and bool(raw_key.strip()),
                "AGENT_HARNESS_INPUT_INVALID",
                "Harness fixture keys must be non-empty strings.",
                status="BLOCKED",
                path=path,
            )
            key = raw_key.strip()
            require(
                key.casefold() not in _HARNESS_FORBIDDEN_INPUT_KEYS,
                "AGENT_HARNESS_COMMAND_INPUT_FORBIDDEN",
                "The in-process harness never accepts a shell, command, or script input.",
                status="BLOCKED",
                path=f"{path}.{key}",
            )
            normalized[key] = _normalize_harness_payload(
                raw_value, path=f"{path}.{key}"
            )
        return dict(sorted(normalized.items()))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [
            _normalize_harness_payload(item, path=f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    require(
        False,
        "AGENT_HARNESS_INPUT_INVALID",
        "Harness fixtures must contain only bounded JSON values.",
        status="BLOCKED",
        path=path,
    )
    raise AssertionError("unreachable")


@dataclass(frozen=True, slots=True)
class AgentHarnessCase:
    """One deterministic, in-process agent-workflow execution fixture."""

    case_id: str
    handler_id: str
    payload: Mapping[str, object]
    expected_outcome: str = "PASS"

    def normalized(self) -> AgentHarnessCase:
        case_id = self.case_id.strip()
        handler_id = self.handler_id.strip()
        expected = self.expected_outcome.strip().upper()
        require(
            bool(_EXECUTION_EVIDENCE_ID.fullmatch(case_id)),
            "AGENT_HARNESS_CASE_INVALID",
            "Harness case_id must be an exact non-secret identifier.",
            status="BLOCKED",
        )
        require(
            bool(_EXECUTION_EVIDENCE_ID.fullmatch(handler_id)),
            "AGENT_HARNESS_CASE_INVALID",
            "Harness handler_id must be an exact registered identifier.",
            status="BLOCKED",
        )
        require(
            expected in _HARNESS_OUTCOMES,
            "AGENT_HARNESS_CASE_INVALID",
            "Harness expected_outcome must be PASS, FAIL, or ERROR.",
            status="BLOCKED",
            expected_outcome=expected,
        )
        payload = _normalize_harness_payload(self.payload)
        require(
            isinstance(payload, dict),
            "AGENT_HARNESS_INPUT_INVALID",
            "A harness fixture payload must be a JSON object.",
            status="BLOCKED",
        )
        return AgentHarnessCase(
            case_id=case_id,
            handler_id=handler_id,
            payload=cast(dict[str, object], payload),
            expected_outcome=expected,
        )


@dataclass(frozen=True, slots=True)
class AgentHarnessResult:
    """Constrained output returned by one registered in-process handler."""

    outcome: str
    output: str

    def normalized(self) -> AgentHarnessResult:
        outcome = self.outcome.strip().upper()
        require(
            outcome in {"PASS", "FAIL"},
            "AGENT_HARNESS_RESULT_INVALID",
            "Registered handlers may return only PASS or FAIL; exceptions are ERROR.",
            status="BLOCKED",
            outcome=outcome,
        )
        require(
            isinstance(self.output, str),
            "AGENT_HARNESS_RESULT_INVALID",
            "Registered handler output must be text.",
            status="BLOCKED",
        )
        return AgentHarnessResult(outcome=outcome, output=self.output)


def run_agent_execution_harness(
    cases: Sequence[AgentHarnessCase],
    *,
    handlers: Mapping[
        str, Callable[[Mapping[str, object]], AgentHarnessResult]
    ],
    max_safe_output_chars: int = _DEFAULT_SAFE_OUTPUT_CHARS,
) -> dict[str, Any]:
    """Run exact registered fixtures and seal a deterministic execution receipt.

    This is the project-authored contract inspired by ``gh-aw-harness``. It is
    deliberately separate from SQLite continuity/retrieval and never interprets
    payload text as a command, shell script, executable, or working directory.
    """

    require(
        not isinstance(cases, (str, bytes)) and bool(cases),
        "AGENT_HARNESS_CASE_SET_EMPTY",
        "At least one exact harness case is required.",
        status="BLOCKED",
    )
    normalized = tuple(item.normalized() for item in cases)
    case_ids = [item.case_id for item in normalized]
    require(
        len(case_ids) == len(set(case_ids)),
        "AGENT_HARNESS_CASE_DUPLICATE",
        "Harness case identifiers must be unique.",
        status="BLOCKED",
    )
    registered_handlers = tuple(sorted(str(item).strip() for item in handlers))
    for handler_id in registered_handlers:
        require(
            bool(_EXECUTION_EVIDENCE_ID.fullmatch(handler_id))
            and callable(handlers[handler_id]),
            "AGENT_HARNESS_HANDLER_INVALID",
            "Every harness handler must have an exact identifier and callable implementation.",
            status="BLOCKED",
            handler_id=handler_id,
        )
    missing = sorted(
        {item.handler_id for item in normalized} - set(registered_handlers)
    )
    require(
        not missing,
        "AGENT_HARNESS_HANDLER_MISSING",
        "A harness case references an unregistered handler.",
        status="BLOCKED",
        missing_handlers=missing,
    )

    case_receipts: list[dict[str, Any]] = []
    failed_case_ids: list[str] = []
    for case in normalized:
        infrastructure_error: str | None = None
        try:
            result = handlers[case.handler_id](case.payload).normalized()
            observed_outcome = result.outcome
            output = result.output
        except Exception as exc:  # noqa: BLE001 - registered fixture failures are evidence
            observed_outcome = "ERROR"
            output = ""
            infrastructure_error = f"{type(exc).__name__}: {exc}"
        inspection = inspect_agent_output(
            output,
            infrastructure_error=infrastructure_error,
            max_safe_chars=max_safe_output_chars,
        )
        outcome_matches = observed_outcome == case.expected_outcome
        security_passes = inspection["threat_status"] == "PASS"
        case_status = "PASS" if outcome_matches and security_passes else "BLOCKED"
        if case_status != "PASS":
            failed_case_ids.append(case.case_id)
        case_receipt = {
            "case_id": case.case_id,
            "handler_id": case.handler_id,
            "payload_sha256": sha256_bytes(canonical_json_bytes(case.payload)),
            "expected_outcome": case.expected_outcome,
            "observed_outcome": observed_outcome,
            "outcome_matches": outcome_matches,
            "security_status": inspection["threat_status"],
            "infrastructure_status": inspection["infrastructure_status"],
            "output_inspection_receipt_sha256": inspection["receipt_sha256"],
            "safe_output": inspection["safe_output"],
            "safe_infrastructure_error": inspection["safe_infrastructure_error"],
            "status": case_status,
        }
        case_receipt["receipt_sha256"] = sha256_bytes(
            canonical_json_bytes(case_receipt)
        )
        case_receipts.append(case_receipt)

    body: dict[str, Any] = {
        "schema": "evidence-lane.agent-execution-harness.v1",
        "status": "PASS" if not failed_case_ids else "BLOCKED",
        "contract": {
            "transport": "IN_PROCESS_REGISTERED_HANDLER",
            "arbitrary_command_input_accepted": False,
            "shell_spawned_by_harness": False,
            "sqlite_continuity_harness_used": False,
            "continuity_or_retrieval_role": False,
            "upstream_code_imported": False,
            "upstream_reference": "github/gh-aw-harness",
            "upstream_reference_commit": GH_AW_HARNESS_REFERENCE_COMMIT,
        },
        "registered_handlers": list(registered_handlers),
        "case_count": len(case_receipts),
        "failed_case_ids": failed_case_ids,
        "cases": case_receipts,
    }
    body["case_set_sha256"] = sha256_bytes(canonical_json_bytes(body["cases"]))
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
            workflow_reference = _parse_uses_value(match.group("value"))
            if workflow_reference is None:
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
                    **validate_action_reference(workflow_reference),
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


def _action_metadata_using(text: str) -> str | None:
    values: list[str] = []
    for line in text.splitlines():
        match = _USING_LINE.match(line)
        if match is None:
            continue
        value = _parse_uses_value(match.group("value"))
        if value is not None:
            values.append(value.strip().lower())
    return values[0] if len(values) == 1 else None


def _action_metadata_path(action_root: Path) -> Path | None:
    candidates = tuple(
        path
        for path in (action_root / "action.yml", action_root / "action.yaml")
        if path.is_file()
    )
    return candidates[0] if len(candidates) == 1 else None


def audit_workflow_action_runtimes(
    workflow_root: str | Path,
    runtime_lock_path: str | Path,
    *,
    repository_root: str | Path,
) -> dict[str, Any]:
    """Audit the complete active Action runtime closure without network access.

    Workflow references are discovered from the executable repository surface.
    Remote metadata identities and transitive ``uses`` references come from one
    reviewed lock whose entries are bound to immutable commit SHAs. Local
    actions are inspected directly from the same repository commit. Any new,
    missing, stale, dynamic, or pre-Node-24 JavaScript action blocks the audit.
    """

    workflows = Path(workflow_root).resolve()
    repository = Path(repository_root).resolve()
    lock_path = Path(runtime_lock_path).resolve()
    require(
        workflows.is_dir() and lock_path.is_file(),
        "GITHUB_ACTION_RUNTIME_AUTHORITY_MISSING",
        "The workflow root and Action runtime lock must both exist.",
        status="BLOCKED",
    )
    require(
        workflows.is_relative_to(repository),
        "GITHUB_ACTION_RUNTIME_ROOT_INVALID",
        "The workflow root must remain inside the governed repository.",
        status="BLOCKED",
    )
    try:
        raw_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("GITHUB_ACTION_RUNTIME_LOCK_INVALID") from exc
    require(
        isinstance(raw_lock, Mapping)
        and set(raw_lock) == {"schema", "actions"}
        and raw_lock.get("schema")
        == "evidence-lane.github-action-runtime-lock.v1"
        and isinstance(raw_lock.get("actions"), list),
        "GITHUB_ACTION_RUNTIME_LOCK_INVALID",
        "The Action runtime lock schema or fields are invalid.",
        status="BLOCKED",
    )

    violations: list[dict[str, Any]] = []
    entries: dict[str, dict[str, Any]] = {}
    expected_entry_fields = {
        "reference",
        "release",
        "using",
        "metadata_path",
        "metadata_sha256",
        "nested_uses",
        "source_url",
    }
    for ordinal, raw_entry in enumerate(cast(list[object], raw_lock["actions"]), 1):
        if not isinstance(raw_entry, Mapping) or set(raw_entry) != expected_entry_fields:
            violations.append(
                {
                    "code": "GITHUB_ACTION_RUNTIME_LOCK_ENTRY_INVALID",
                    "ordinal": ordinal,
                }
            )
            continue
        entry = {str(key): value for key, value in raw_entry.items()}
        reference = str(entry["reference"]).strip()
        release = str(entry["release"]).strip()
        using = str(entry["using"]).strip().lower()
        metadata_path = str(entry["metadata_path"]).strip().replace("\\", "/")
        metadata_sha256 = str(entry["metadata_sha256"]).strip().upper()
        source_url = str(entry["source_url"]).strip()
        nested_raw = entry["nested_uses"]
        reference_result = validate_action_reference(reference)
        valid = (
            reference_result["kind"] == "REMOTE_FULL_COMMIT_SHA"
            and bool(release)
            and len(release.encode("utf-8")) <= 64
            and bool(metadata_path)
            and not metadata_path.startswith("/")
            and ".." not in Path(metadata_path).parts
            and bool(_ACTION_RUNTIME_SHA256.fullmatch(metadata_sha256))
            and source_url.startswith("https://github.com/")
            and isinstance(nested_raw, list)
            and all(isinstance(item, str) for item in cast(list[object], nested_raw))
        )
        if not valid:
            violations.append(
                {
                    "code": "GITHUB_ACTION_RUNTIME_LOCK_ENTRY_INVALID",
                    "ordinal": ordinal,
                    "reference": reference,
                }
            )
            continue
        if reference in entries:
            violations.append(
                {
                    "code": "GITHUB_ACTION_RUNTIME_LOCK_DUPLICATE",
                    "reference": reference,
                }
            )
            continue
        if using not in _ACTION_RUNTIME_ALLOWED:
            violations.append(
                {
                    "code": (
                        "GITHUB_ACTION_RUNTIME_NODE20_DEPRECATED"
                        if using == "node20"
                        else "GITHUB_ACTION_RUNTIME_UNSUPPORTED"
                    ),
                    "reference": reference,
                    "using": using,
                }
            )
        entries[reference] = {
            "reference": reference,
            "release": release,
            "using": using,
            "metadata_path": metadata_path,
            "metadata_sha256": metadata_sha256,
            "nested_uses": tuple(str(item).strip() for item in nested_raw),
            "source_url": source_url,
        }

    workflow_paths = tuple(
        sorted(
            {
                path
                for pattern in ("*.yml", "*.yaml")
                for path in workflows.rglob(pattern)
                if path.is_file()
            }
        )
    )
    require(
        bool(workflow_paths),
        "WORKFLOW_SET_EMPTY",
        "No active workflow files were available for runtime audit.",
        status="BLOCKED",
    )
    direct_references: list[str] = []
    for path in workflow_paths:
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8", errors="strict").splitlines(), 1
        ):
            match = _USES_LINE.match(line)
            if match is None:
                continue
            direct_reference = _parse_uses_value(match.group("value"))
            if direct_reference is None:
                violations.append(
                    {
                        "code": "WORKFLOW_ACTION_REF_UNPARSEABLE",
                        "path": path.relative_to(workflows).as_posix(),
                        "line": line_number,
                    }
                )
                continue
            direct_references.append(direct_reference)

    remote_pending: list[str] = []
    local_pending: list[str] = []
    docker_references: set[str] = set()
    for reference in direct_references:
        finding = validate_action_reference(reference)
        if finding["kind"] == "REMOTE_FULL_COMMIT_SHA":
            remote_pending.append(reference)
        elif finding["kind"] == "LOCAL_SAME_COMMIT":
            local_pending.append(reference)
        elif finding["kind"] == "DOCKER_SHA256_DIGEST":
            docker_references.add(reference)
        else:
            violations.append(
                {
                    "code": str(finding.get("code", "WORKFLOW_ACTION_REF_INVALID")),
                    "reference": reference,
                }
            )

    reachable_remote: set[str] = set()
    reachable_local: set[str] = set()
    runtime_counts: dict[str, int] = {}
    while remote_pending:
        reference = remote_pending.pop()
        if reference in reachable_remote:
            continue
        reachable_remote.add(reference)
        runtime_entry = entries.get(reference)
        if runtime_entry is None:
            violations.append(
                {
                    "code": "GITHUB_ACTION_RUNTIME_UNVERIFIED",
                    "reference": reference,
                }
            )
            continue
        using = str(runtime_entry["using"])
        runtime_counts[using] = runtime_counts.get(using, 0) + 1
        for nested in cast(tuple[str, ...], runtime_entry["nested_uses"]):
            finding = validate_action_reference(nested)
            if finding["kind"] == "REMOTE_FULL_COMMIT_SHA":
                remote_pending.append(nested)
            elif finding["kind"] == "DOCKER_SHA256_DIGEST":
                docker_references.add(nested)
            else:
                violations.append(
                    {
                        "code": "GITHUB_ACTION_TRANSITIVE_REF_UNVERIFIED",
                        "parent_reference": reference,
                        "reference": nested,
                    }
                )

    while local_pending:
        reference = local_pending.pop()
        if reference in reachable_local:
            continue
        reachable_local.add(reference)
        action_root = (repository / Path(reference[2:])).resolve()
        if not action_root.is_relative_to(repository):
            violations.append(
                {"code": "GITHUB_LOCAL_ACTION_PATH_ESCAPE", "reference": reference}
            )
            continue
        metadata = _action_metadata_path(action_root)
        if metadata is None:
            violations.append(
                {"code": "GITHUB_LOCAL_ACTION_METADATA_INVALID", "reference": reference}
            )
            continue
        text = metadata.read_text(encoding="utf-8", errors="strict")
        local_using = _action_metadata_using(text)
        if local_using is None or local_using not in _ACTION_RUNTIME_ALLOWED:
            violations.append(
                {
                    "code": (
                        "GITHUB_ACTION_RUNTIME_NODE20_DEPRECATED"
                        if local_using == "node20"
                        else "GITHUB_ACTION_RUNTIME_UNSUPPORTED"
                    ),
                    "reference": reference,
                    "using": local_using or "UNPARSEABLE",
                }
            )
            continue
        runtime_counts[local_using] = runtime_counts.get(local_using, 0) + 1
        if local_using == "composite":
            for line in text.splitlines():
                match = _USES_LINE.match(line)
                if match is None:
                    continue
                local_nested = _parse_uses_value(match.group("value"))
                if local_nested is None:
                    violations.append(
                        {
                            "code": "GITHUB_ACTION_TRANSITIVE_REF_UNVERIFIED",
                            "parent_reference": reference,
                            "reference": "UNPARSEABLE",
                        }
                    )
                    continue
                finding = validate_action_reference(local_nested)
                if finding["kind"] == "REMOTE_FULL_COMMIT_SHA":
                    remote_pending.append(local_nested)
                elif finding["kind"] == "LOCAL_SAME_COMMIT":
                    local_pending.append(local_nested)
                elif finding["kind"] == "DOCKER_SHA256_DIGEST":
                    docker_references.add(local_nested)
                else:
                    violations.append(
                        {
                            "code": "GITHUB_ACTION_TRANSITIVE_REF_UNVERIFIED",
                            "parent_reference": reference,
                            "reference": local_nested,
                        }
                    )

    # Local composite actions may introduce additional immutable remote
    # references. Close that final edge set before comparing it with the lock.
    while remote_pending:
        reference = remote_pending.pop()
        if reference in reachable_remote:
            continue
        reachable_remote.add(reference)
        runtime_entry = entries.get(reference)
        if runtime_entry is None:
            violations.append(
                {
                    "code": "GITHUB_ACTION_RUNTIME_UNVERIFIED",
                    "reference": reference,
                }
            )
            continue
        using = str(runtime_entry["using"])
        runtime_counts[using] = runtime_counts.get(using, 0) + 1
        for nested in cast(tuple[str, ...], runtime_entry["nested_uses"]):
            finding = validate_action_reference(nested)
            if finding["kind"] == "REMOTE_FULL_COMMIT_SHA":
                remote_pending.append(nested)
            elif finding["kind"] == "DOCKER_SHA256_DIGEST":
                docker_references.add(nested)
            else:
                violations.append(
                    {
                        "code": "GITHUB_ACTION_TRANSITIVE_REF_UNVERIFIED",
                        "parent_reference": reference,
                        "reference": nested,
                    }
                )

    for reference in sorted(set(entries) - reachable_remote):
        violations.append(
            {
                "code": "GITHUB_ACTION_RUNTIME_LOCK_ENTRY_UNREFERENCED",
                "reference": reference,
            }
        )
    body: dict[str, Any] = {
        "schema": "evidence-lane.github-action-runtime-audit.v1",
        "status": "PASS" if not violations else "BLOCKED",
        "workflow_file_count": len(workflow_paths),
        "direct_reference_count": len(direct_references),
        "remote_action_count": len(reachable_remote),
        "local_action_count": len(reachable_local),
        "docker_digest_count": len(docker_references),
        "runtime_counts": dict(sorted(runtime_counts.items())),
        "node20_count": runtime_counts.get("node20", 0),
        "violation_count": len(violations),
        "violations": violations,
        "runtime_lock_sha256": sha256_file(lock_path),
        "reference_closure_sha256": sha256_bytes(
            canonical_json_bytes(
                {
                    "remote": sorted(reachable_remote),
                    "local": sorted(reachable_local),
                    "docker": sorted(docker_references),
                }
            )
        ),
    }
    body["audit_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body

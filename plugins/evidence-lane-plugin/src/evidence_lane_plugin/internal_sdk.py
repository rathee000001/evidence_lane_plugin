"""Private provider-neutral SDK boundary for the governed Codex layer.

The SDK is an internal engine and contract surface, not a public distribution.
Every authority arm keeps its own namespace, operation catalog, replay ledger,
permissions, and result slice.  The top-level client only validates and routes;
it never fuses Project Truth, Canon Input, Agent Learning, ChatLineage, or
host-entry continuity into one authority.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from .agent_learning import (
    decide_learning_candidate,
    inspect_learning_authority,
    retrieve_accepted_learning,
    revoke_learning_candidate,
    seal_learning_candidate,
)
from .canon_task_graph import (
    CanonTaskDispatcher,
    bind_received_canon_task_edge,
    classify_canon_envelope,
    decide_canon_input,
    dispatch_linked_canon_task,
    inspect_canon_authority,
    inspect_canon_inbox,
    inspect_canon_task_graph,
    raise_canon_backfire,
    receive_canon_envelope,
    register_canon_task_edge,
    register_expected_canon_contract,
    restore_canon_state_travel_continuity,
    seal_canon_envelope,
    seal_canon_state_travel_continuity,
    seal_canon_task_result,
    supersede_canon_input,
)
from .errors import EvidenceLaneError, require
from .hashing import canonical_json_bytes, sha256_bytes
from .host_entry_continuity import (
    derive_host_entry_env_uop,
    inspect_host_entry_continuity,
)
from .lineage import ChatLineage
from .redaction import contains_secret

INTERNAL_SDK_ABI = "evidence-lane.internal-sdk.v1"
INTERNAL_SDK_RESPONSE_SCHEMA = "evidence-lane.internal-sdk-response.v1"
INTERNAL_SDK_REPLAY_SCHEMA = "evidence-lane.internal-sdk-replay.v1"
INTERNAL_SDK_MAX_PAYLOAD_BYTES = 256 * 1024
INTERNAL_SDK_MAX_TIMEOUT_MS = 60_000
INTERNAL_SDK_HANDLER_PARITY_SCHEMA = "evidence-lane.sdk-handler-parity.v1"

_SHA256_RE = re.compile(r"^[A-F0-9]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,191}$")
_PV_RE = re.compile(r"^PV[1-9][0-9]*$")
_SECRET_KEY_RE = re.compile(
    r"(?i)(?:^|[_-])(authorization|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|password|private[_-]?key|client[_-]?secret)(?:$|[_-])"
)


@dataclass(frozen=True, slots=True)
class SDKOperationSpec:
    name: str
    effect: str


@dataclass(frozen=True, slots=True)
class SDKModuleSpec:
    module_id: str
    namespace: str
    authority: str
    operations: tuple[SDKOperationSpec, ...]

    def operation(self, name: str) -> SDKOperationSpec | None:
        return next((item for item in self.operations if item.name == name), None)


def _operations(*values: str) -> tuple[SDKOperationSpec, ...]:
    result: list[SDKOperationSpec] = []
    for value in values:
        name, effect = value.split(":", maxsplit=1)
        result.append(SDKOperationSpec(name=name, effect=effect))
    return tuple(result)


SDK_MODULES: tuple[SDKModuleSpec, ...] = (
    SDKModuleSpec(
        "project_truth",
        "sdk.project-truth.v1",
        "PROJECT_TRUTH",
        _operations(
            "status:READ",
            "search:READ",
            "fetch:READ",
            "query:READ",
            "diff:READ",
        ),
    ),
    SDKModuleSpec(
        "canon_input",
        "sdk.canon-input.v1",
        "CANON_INPUT",
        _operations(
            "inspect:READ",
            "inbox:READ",
            "graph:READ",
            "register_contract:WRITE_CANON",
            "seal_envelope:WRITE_CANON",
            "receive:WRITE_CANON",
            "classify:WRITE_CANON",
            "decide:WRITE_CANON",
            "supersede:WRITE_CANON",
            "register_edge:WRITE_CANON",
            "bind_edge:WRITE_CANON",
            "dispatch_linked_task:WRITE_CANON",
            "backfire_hil:WRITE_CANON",
            "seal_result:WRITE_CANON",
            "seal_continuity:WRITE_CANON",
            "restore_continuity:WRITE_CANON",
        ),
    ),
    SDKModuleSpec(
        "agent_learning",
        "sdk.agent-learning.v1",
        "AGENT_LEARNING",
        _operations(
            "inspect:READ",
            "retrieve:READ",
            "seal_candidate:WRITE_LEARNING",
            "decide_candidate:WRITE_LEARNING",
            "revoke:WRITE_LEARNING",
        ),
    ),
    SDKModuleSpec(
        "chat_lineage",
        "sdk.chat-lineage.v1",
        "CHAT_LINEAGE",
        _operations("status:READ", "events:READ", "append:WRITE_LINEAGE"),
    ),
    SDKModuleSpec(
        "host_entry_continuity",
        "sdk.host-entry-continuity.v1",
        "HOST_ENTRY_CONTINUITY",
        _operations(
            "inspect:READ",
            "issue:WRITE_HOST_ENTRY",
            "consume:WRITE_HOST_ENTRY",
            "roll_generation:WRITE_HOST_ENTRY",
        ),
    ),
    SDKModuleSpec(
        "lifecycle_hooks",
        "sdk.lifecycle-hooks.v1",
        "LIFECYCLE_HOOKS",
        _operations(
            "doctor:READ",
            "transition_law:READ",
            "runtime_status:READ",
        ),
    ),
    SDKModuleSpec(
        "plan_delta_tasks",
        "sdk.plan-delta-tasks.v1",
        "PLAN_DELTA_TASKS",
        _operations(
            "status:READ",
            "backlog:READ",
            "transition_task:WRITE_PLAN",
            "project_host_plan:WRITE_HOST_PLAN",
        ),
    ),
    SDKModuleSpec(
        "source_lane_retrieval",
        "sdk.source-lane-retrieval.v1",
        "SOURCE_LANE_RETRIEVAL",
        _operations(
            "search:READ",
            "fetch:READ",
            "query:READ",
            "source_intake:WRITE_SOURCE",
        ),
    ),
    SDKModuleSpec(
        "env_uop_operator_runtime",
        "sdk.env-uop-operator-runtime.v1",
        "ENV_UOP_FORMULA_PCM_MBA",
        _operations(
            "status:READ",
            "classify_mode:WRITE_LINEAGE",
            "compile_formula:WRITE_DERIVED_OPERATOR",
            "route_operator:WRITE_DERIVED_OPERATOR",
        ),
    ),
    SDKModuleSpec(
        "storage_connectors",
        "sdk.storage-connectors.v1",
        "STORAGE_CONNECTORS",
        _operations(
            "inspect:READ",
            "select:WRITE_STORAGE_SELECTION",
            "plugin_catalog:READ",
            "plugin_route:READ",
        ),
    ),
    SDKModuleSpec(
        "hil_candidate_pointer",
        "sdk.hil-candidate-pointer.v1",
        "HIL_CANDIDATE_POINTER",
        _operations(
            "status:READ",
            "render_hil:READ",
            "record_decision:WRITE_HIL",
            "fuse:WRITE_PROJECT_TRUTH",
            "rollback:WRITE_PROJECT_TRUTH",
        ),
    ),
    SDKModuleSpec(
        "provider_host_adapters",
        "sdk.provider-host-adapters.v1",
        "PROVIDER_HOST_ADAPTERS",
        _operations(
            "capabilities:READ",
            "binding_status:READ",
            "invoke_headless:WRITE_PROVIDER",
        ),
    ),
)

_MODULE_BY_ID = {item.module_id: item for item in SDK_MODULES}
SDK_EXTERNAL_PROVIDER_OPERATIONS = {
    ("host_entry_continuity", "issue"): "codex-host-entry-provider.v1",
    ("host_entry_continuity", "consume"): "codex-host-entry-provider.v1",
    (
        "host_entry_continuity",
        "roll_generation",
    ): "codex-host-entry-provider.v1",
    ("plan_delta_tasks", "project_host_plan"): "codex-host-plan-provider.v1",
    (
        "env_uop_operator_runtime",
        "compile_formula",
    ): "env-uop-operator-provider.v1",
    (
        "env_uop_operator_runtime",
        "route_operator",
    ): "env-uop-operator-provider.v1",
    (
        "provider_host_adapters",
        "invoke_headless",
    ): "codex-headless-provider.v1",
}
SDK_NARROWED_OPERATION_CLAIMS = {
    "lifecycle_hooks:boot_or_resume": (
        "Ambiguous combined lifecycle write removed; native Boot and Resume routes "
        "retain their separate contracts."
    ),
    "lifecycle_hooks:record_visible_event": (
        "Duplicate lineage write removed; chat_lineage:append is the owning ABI."
    ),
    "lifecycle_hooks:seal_exit_entry": (
        "Undefined combined exit write removed; host-entry providers own exact slips."
    ),
    "plan_delta_tasks:classify_delta": (
        "Unbound classifier claim removed; native Plan ingestion owns classification."
    ),
    "hil_candidate_pointer:prepare_candidate": (
        "Ambiguous build-or-refresh claim removed; lifecycle candidate routes remain "
        "separate."
    ),
}


def _operation_execution_contract(
    module_id: str, operation: str
) -> dict[str, Any]:
    provider = SDK_EXTERNAL_PROVIDER_OPERATIONS.get((module_id, operation))
    return {
        "execution_owner": (
            "EXTERNAL_PROVIDER_ADAPTER" if provider else "LOCAL_SERVICE_HANDLER"
        ),
        "provider_adapter_id": provider,
    }
_AUTHORITY_EFFECT_KEYS = (
    "project_truth",
    "canon_input",
    "agent_learning",
    "chat_lineage",
    "host_entry_continuity",
)
_MODULE_OWNED_EFFECT = {
    "project_truth": "project_truth",
    "canon_input": "canon_input",
    "agent_learning": "agent_learning",
    "chat_lineage": "chat_lineage",
    "host_entry_continuity": "host_entry_continuity",
    "lifecycle_hooks": None,
    "plan_delta_tasks": None,
    "source_lane_retrieval": None,
    "env_uop_operator_runtime": None,
    "storage_connectors": None,
    "hil_candidate_pointer": "project_truth",
    "provider_host_adapters": None,
}
_OPERATION_OWNED_EFFECT = {
    "WRITE_CANON": "canon_input",
    "WRITE_LEARNING": "agent_learning",
    "WRITE_LINEAGE": "chat_lineage",
    "WRITE_HOST_ENTRY": "host_entry_continuity",
    "WRITE_PROJECT_TRUTH": "project_truth",
}


def _exact_text(value: Any, *, field: str) -> str:
    exact = str(value or "").strip()
    require(
        bool(exact),
        "SDK_BINDING_FIELD_REQUIRED",
        "Every SDK invocation requires a complete exact binding.",
        status="BLOCKED",
        field=field,
    )
    return exact


def _sha256(value: Any, *, field: str) -> str:
    exact = _exact_text(value, field=field).upper()
    require(
        bool(_SHA256_RE.fullmatch(exact)),
        "SDK_BINDING_SHA256_INVALID",
        "An SDK binding field is not one exact SHA-256.",
        status="MISMATCH",
        field=field,
    )
    return exact


def _secret_paths(value: Any, prefix: str = "payload") -> list[str]:
    paths: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            exact_key = str(key)
            child = f"{prefix}.{exact_key}"
            if _SECRET_KEY_RE.search(exact_key):
                paths.append(child)
            paths.extend(_secret_paths(item, child))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            paths.extend(_secret_paths(item, f"{prefix}[{index}]"))
    elif contains_secret(value):
        paths.append(prefix)
    return paths


@dataclass(frozen=True, slots=True)
class SDKBinding:
    project_id: str
    session_id: str
    task_id: str
    accepted_pv: str
    pointer_generation: int
    accepted_manifest_sha256: str
    lineage_head_sha256: str
    env_authority_sha256: str
    uop_authority_sha256: str
    derived_projection_sha256: str
    flash_receipt_sha256: str
    model: str
    submodel: str
    reasoning_effort: str
    reasoning_speed: str
    host_kind: str
    host_session_id: str
    write_scope: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> SDKBinding:
        required = {
            "project_id",
            "session_id",
            "task_id",
            "accepted_pv",
            "pointer_generation",
            "accepted_manifest_sha256",
            "lineage_head_sha256",
            "env_authority_sha256",
            "uop_authority_sha256",
            "derived_projection_sha256",
            "flash_receipt_sha256",
            "model",
            "submodel",
            "reasoning_effort",
            "reasoning_speed",
            "host_kind",
            "host_session_id",
            "write_scope",
        }
        require(
            set(value) == required,
            "SDK_BINDING_SHAPE_INVALID",
            "The SDK binding must contain exactly the versioned identity fields.",
            status="MISMATCH",
            missing=sorted(required - set(value)),
            extra=sorted(set(value) - required),
        )
        accepted_pv = _exact_text(value["accepted_pv"], field="accepted_pv")
        require(
            bool(_PV_RE.fullmatch(accepted_pv)),
            "SDK_ACCEPTED_PV_INVALID",
            "The SDK binding requires one accepted PV identity.",
            status="MISMATCH",
        )
        try:
            pointer_generation = int(value["pointer_generation"])
        except (TypeError, ValueError) as exc:
            raise EvidenceLaneError(
                "SDK_POINTER_GENERATION_INVALID",
                "The SDK pointer generation must be an integer.",
                status="MISMATCH",
            ) from exc
        require(
            pointer_generation >= 1,
            "SDK_POINTER_GENERATION_INVALID",
            "The SDK pointer generation must be positive.",
            status="MISMATCH",
        )
        raw_scope = value["write_scope"]
        require(
            isinstance(raw_scope, (list, tuple)),
            "SDK_WRITE_SCOPE_INVALID",
            "SDK write scope must be an ordered list of exact grants.",
            status="BLOCKED",
        )
        write_scope = tuple(
            _exact_text(item, field="write_scope") for item in raw_scope
        )
        require(
            len(write_scope) == len(set(write_scope)),
            "SDK_WRITE_SCOPE_DUPLICATE",
            "SDK write grants must be unique.",
            status="BLOCKED",
        )
        result = cls(
            project_id=_exact_text(value["project_id"], field="project_id"),
            session_id=_exact_text(value["session_id"], field="session_id"),
            task_id=_exact_text(value["task_id"], field="task_id"),
            accepted_pv=accepted_pv,
            pointer_generation=pointer_generation,
            accepted_manifest_sha256=_sha256(
                value["accepted_manifest_sha256"], field="accepted_manifest_sha256"
            ),
            lineage_head_sha256=_sha256(
                value["lineage_head_sha256"], field="lineage_head_sha256"
            ),
            env_authority_sha256=_sha256(
                value["env_authority_sha256"], field="env_authority_sha256"
            ),
            uop_authority_sha256=_sha256(
                value["uop_authority_sha256"], field="uop_authority_sha256"
            ),
            derived_projection_sha256=_sha256(
                value["derived_projection_sha256"],
                field="derived_projection_sha256",
            ),
            flash_receipt_sha256=_sha256(
                value["flash_receipt_sha256"], field="flash_receipt_sha256"
            ),
            model=_exact_text(value["model"], field="model"),
            submodel=_exact_text(value["submodel"], field="submodel"),
            reasoning_effort=_exact_text(
                value["reasoning_effort"], field="reasoning_effort"
            ),
            reasoning_speed=_exact_text(
                value["reasoning_speed"], field="reasoning_speed"
            ),
            host_kind=_exact_text(value["host_kind"], field="host_kind"),
            host_session_id=_exact_text(
                value["host_session_id"], field="host_session_id"
            ),
            write_scope=write_scope,
        )
        require(
            not _secret_paths(result.as_dict(), "binding"),
            "SDK_BINDING_SECRET_FORBIDDEN",
            "Secret material cannot enter an SDK binding.",
            status="BLOCKED",
        )
        return result

    def as_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "accepted_pv": self.accepted_pv,
            "pointer_generation": self.pointer_generation,
            "accepted_manifest_sha256": self.accepted_manifest_sha256,
            "lineage_head_sha256": self.lineage_head_sha256,
            "env_authority_sha256": self.env_authority_sha256,
            "uop_authority_sha256": self.uop_authority_sha256,
            "derived_projection_sha256": self.derived_projection_sha256,
            "flash_receipt_sha256": self.flash_receipt_sha256,
            "model": self.model,
            "submodel": self.submodel,
            "reasoning_effort": self.reasoning_effort,
            "reasoning_speed": self.reasoning_speed,
            "host_kind": self.host_kind,
            "host_session_id": self.host_session_id,
            "write_scope": list(self.write_scope),
        }

    @property
    def sha256(self) -> str:
        return sha256_bytes(canonical_json_bytes(self.as_dict()))


class SDKCancellationToken:
    """Cooperative cancellation shared by the router and provider adapter."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def require_active(self) -> None:
        require(
            not self.cancelled,
            "SDK_INVOCATION_CANCELLED",
            "The SDK invocation was cancelled before a governed effect.",
            status="CANCELLED",
        )


@dataclass(frozen=True, slots=True)
class SDKInvocationContext:
    request_id: str
    timeout_ms: int
    started_monotonic: float
    cancellation: SDKCancellationToken

    @property
    def remaining_ms(self) -> int:
        elapsed = int((time.monotonic() - self.started_monotonic) * 1000)
        return max(0, self.timeout_ms - elapsed)

    def checkpoint(self) -> None:
        self.cancellation.require_active()
        require(
            self.remaining_ms > 0,
            "SDK_INVOCATION_TIMEOUT",
            "The SDK invocation exceeded its bounded timeout.",
            status="TIMEOUT",
            timeout_ms=self.timeout_ms,
        )


class InternalSDKAdapter(Protocol):
    adapter_id: str

    def available_operations(self) -> Mapping[str, set[str]]: ...

    def binding_snapshot(self, binding: SDKBinding) -> Mapping[str, Any]: ...

    def invoke(
        self,
        module_id: str,
        operation: str,
        binding: SDKBinding,
        payload: dict[str, Any],
        context: SDKInvocationContext,
    ) -> dict[str, Any]: ...


SDKHandler = Callable[
    [SDKBinding, dict[str, Any], SDKInvocationContext], dict[str, Any]
]


class RegisteredSDKAdapter:
    """Small provider adapter assembled from explicit operation handlers."""

    def __init__(
        self,
        *,
        adapter_id: str,
        snapshot_provider: Callable[[SDKBinding], Mapping[str, Any]],
        handlers: Mapping[tuple[str, str], SDKHandler],
    ) -> None:
        require(
            bool(_SAFE_ID_RE.fullmatch(adapter_id)),
            "SDK_ADAPTER_ID_INVALID",
            "An SDK adapter requires one stable public-safe identity.",
            status="BLOCKED",
        )
        self.adapter_id = adapter_id
        self._snapshot_provider = snapshot_provider
        self._handlers = dict(handlers)
        for module_id, operation in self._handlers:
            module = _MODULE_BY_ID.get(module_id)
            require(
                module is not None and module.operation(operation) is not None,
                "SDK_ADAPTER_OPERATION_UNKNOWN",
                "An adapter registered an operation outside the SDK ABI.",
                status="MISMATCH",
                module_id=module_id,
                operation=operation,
            )

    def available_operations(self) -> Mapping[str, set[str]]:
        result: dict[str, set[str]] = {}
        for module_id, operation in self._handlers:
            result.setdefault(module_id, set()).add(operation)
        return result

    def binding_snapshot(self, binding: SDKBinding) -> Mapping[str, Any]:
        return self._snapshot_provider(binding)

    def invoke(
        self,
        module_id: str,
        operation: str,
        binding: SDKBinding,
        payload: dict[str, Any],
        context: SDKInvocationContext,
    ) -> dict[str, Any]:
        return self._handlers[(module_id, operation)](binding, payload, context)


def inspect_sdk_handler_parity(
    adapter: InternalSDKAdapter,
    *,
    construction_profile: str,
) -> dict[str, Any]:
    """Classify every ABI operation against one exact adapter construction."""

    exact_profile = _exact_text(
        construction_profile, field="construction_profile"
    )
    declared = {
        (module.module_id, operation.name)
        for module in SDK_MODULES
        for operation in module.operations
    }
    local_required = declared - set(SDK_EXTERNAL_PROVIDER_OPERATIONS)
    available = adapter.available_operations()
    provided = {
        (module_id, operation)
        for module_id, operations in available.items()
        for operation in operations
    }
    unknown = sorted(provided - declared)
    missing_local = sorted(local_required - provided)
    external_claimed = sorted(provided & set(SDK_EXTERNAL_PROVIDER_OPERATIONS))
    require(
        not unknown and not missing_local,
        "SDK_HANDLER_PARITY_MISMATCH",
        "The adapter construction does not cover every locally owned SDK operation.",
        status="MISMATCH",
        adapter_id=adapter.adapter_id,
        construction_profile=exact_profile,
        unknown_operations=[f"{module}:{operation}" for module, operation in unknown],
        missing_local_handlers=[
            f"{module}:{operation}" for module, operation in missing_local
        ],
    )
    if exact_profile == "LOCAL_SERVICE":
        require(
            not external_claimed,
            "SDK_EXTERNAL_PROVIDER_IMPERSONATION_BLOCKED",
            "The local service adapter cannot claim host/provider-owned operations.",
            status="BLOCKED",
            operations=[
                f"{module}:{operation}" for module, operation in external_claimed
            ],
        )
    operation_rows = []
    for module in SDK_MODULES:
        for operation in module.operations:
            key = (module.module_id, operation.name)
            contract = _operation_execution_contract(*key)
            operation_rows.append(
                {
                    "module_id": module.module_id,
                    "operation": operation.name,
                    "effect": operation.effect,
                    **contract,
                    "handler_registered": key in provided,
                    "status": (
                        "REGISTERED"
                        if key in provided
                        else "EXTERNAL_PROVIDER_REQUIRED"
                    ),
                }
            )
    body = {
        "schema": INTERNAL_SDK_HANDLER_PARITY_SCHEMA,
        "status": "PASS",
        "abi": INTERNAL_SDK_ABI,
        "adapter_id": adapter.adapter_id,
        "construction_profile": exact_profile,
        "declared_operation_count": len(declared),
        "registered_local_handler_count": len(provided),
        "external_provider_operation_count": len(
            SDK_EXTERNAL_PROVIDER_OPERATIONS
        ),
        "narrowed_operation_claim_count": len(SDK_NARROWED_OPERATION_CLAIMS),
        "unclassified_operation_count": 0,
        "operations": operation_rows,
        "narrowed_operation_claims": dict(SDK_NARROWED_OPERATION_CLAIMS),
    }
    return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


class InternalEvidenceLaneSDK:
    """Validated router over isolated authority modules and provider adapters."""

    def __init__(self, project_root: str | Path, adapter: InternalSDKAdapter) -> None:
        self.project_root = Path(project_root).resolve()
        self.adapter = adapter

    @staticmethod
    def module_catalog() -> dict[str, Any]:
        return {
            "status": "PASS",
            "abi": INTERNAL_SDK_ABI,
            "distribution": "PRIVATE_INTERNAL_CODEX_LAYER",
            "top_level_role": "ROUTE_DISCOVER_VALIDATE_ONLY",
            "authority_merge_allowed": False,
            "modules": [
                {
                    "module_id": module.module_id,
                    "namespace": module.namespace,
                    "authority": module.authority,
                    "operations": [
                        {
                            "name": operation.name,
                            "effect": operation.effect,
                            **_operation_execution_contract(
                                module.module_id, operation.name
                            ),
                        }
                        for operation in module.operations
                    ],
                    "independent_replay_ledger": True,
                }
                for module in SDK_MODULES
            ],
            "narrowed_operation_claims": dict(SDK_NARROWED_OPERATION_CLAIMS),
        }

    def capability_status(self) -> dict[str, Any]:
        available = self.adapter.available_operations()
        modules: list[dict[str, Any]] = []
        for module in SDK_MODULES:
            provided = available.get(module.module_id, set())
            external = {
                operation.name: SDK_EXTERNAL_PROVIDER_OPERATIONS[
                    (module.module_id, operation.name)
                ]
                for operation in module.operations
                if (module.module_id, operation.name)
                in SDK_EXTERNAL_PROVIDER_OPERATIONS
            }
            modules.append(
                {
                    "module_id": module.module_id,
                    "namespace": module.namespace,
                    "contract_operations": [item.name for item in module.operations],
                    "available_operations": sorted(provided),
                    "unavailable_operations": sorted(
                        item.name
                        for item in module.operations
                        if item.name not in provided
                    ),
                    "external_provider_operations": external,
                    "unclassified_operations": sorted(
                        item.name
                        for item in module.operations
                        if item.name not in provided
                        and (module.module_id, item.name)
                        not in SDK_EXTERNAL_PROVIDER_OPERATIONS
                    ),
                }
            )
        return {
            "status": "PASS",
            "abi": INTERNAL_SDK_ABI,
            "adapter_id": self.adapter.adapter_id,
            "modules": modules,
            "unsupported_operations_are_explicit": True,
            "external_provider_ownership_is_explicit": True,
        }

    def _ledger_path(self, module: SDKModuleSpec) -> Path:
        return self.project_root / "sdk" / "replay" / f"{module.namespace}.sqlite"

    def _connect(self, module: SDKModuleSpec) -> sqlite3.Connection:
        path = self._ledger_path(module)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sdk_replay(
                request_id TEXT PRIMARY KEY,
                request_sha256 TEXT NOT NULL,
                binding_sha256 TEXT NOT NULL,
                response_sha256 TEXT NOT NULL,
                response_json TEXT NOT NULL
            ) STRICT
            """
        )
        return connection

    @staticmethod
    def _request_id(value: str) -> str:
        exact = str(value or "").strip()
        require(
            bool(_SAFE_ID_RE.fullmatch(exact)),
            "SDK_REQUEST_ID_INVALID",
            "An SDK call requires one stable public-safe request ID.",
            status="BLOCKED",
        )
        return exact

    @staticmethod
    def _binding(value: SDKBinding | Mapping[str, Any]) -> SDKBinding:
        return value if isinstance(value, SDKBinding) else SDKBinding.from_dict(value)

    def _validate_binding(self, binding: SDKBinding) -> None:
        require(
            self.project_root.name == binding.project_id,
            "SDK_PROJECT_NAMESPACE_MISMATCH",
            "The SDK storage namespace does not match the bound project.",
            status="MISMATCH",
            project_root=str(self.project_root),
            project_id=binding.project_id,
        )
        actual = dict(self.adapter.binding_snapshot(binding))
        expected = binding.as_dict()
        missing = sorted(set(expected) - set(actual))
        mismatches = {
            key: {"expected": expected[key], "actual": actual.get(key)}
            for key in expected
            if key in actual and actual[key] != expected[key]
        }
        require(
            not missing and not mismatches,
            "SDK_BINDING_MISMATCH",
            "The provider snapshot does not match the exact SDK call binding.",
            status="MISMATCH",
            missing=missing,
            mismatches=mismatches,
        )

    @staticmethod
    def _validate_effects(
        module: SDKModuleSpec,
        operation: SDKOperationSpec,
        result: dict[str, Any],
    ) -> dict[str, str]:
        raw = result.get("authority_effects") or {
            key: "NONE" for key in _AUTHORITY_EFFECT_KEYS
        }
        require(
            isinstance(raw, dict) and set(raw) == set(_AUTHORITY_EFFECT_KEYS),
            "SDK_AUTHORITY_EFFECTS_INVALID",
            "Every SDK response must keep authority effects explicit and separate.",
            status="MISMATCH",
        )
        effects = {key: str(raw[key]).strip().upper() for key in _AUTHORITY_EFFECT_KEYS}
        owned = _OPERATION_OWNED_EFFECT.get(
            operation.effect,
            _MODULE_OWNED_EFFECT[module.module_id],
        )
        changed = {key for key, value in effects.items() if value != "NONE"}
        require(
            operation.effect != "READ" or not changed,
            "SDK_READ_OPERATION_MUTATED_AUTHORITY",
            "A read SDK operation reported an authority mutation.",
            status="FAIL",
            changed=sorted(changed),
        )
        require(
            not changed or (owned is not None and changed == {owned}),
            "SDK_CROSS_AUTHORITY_EFFECT_BLOCKED",
            "An SDK module cannot mutate another authority arm.",
            status="BLOCKED",
            module_id=module.module_id,
            changed=sorted(changed),
        )
        if module.module_id in {"canon_input", "agent_learning"}:
            require(
                effects["project_truth"] == "NONE",
                "SDK_PROJECT_TRUTH_PROMOTION_BLOCKED",
                "Canon Input and Agent Learning cannot promote Project Truth.",
                status="BLOCKED",
            )
        return effects

    def invoke(
        self,
        *,
        module_id: str,
        operation: str,
        binding: SDKBinding | Mapping[str, Any],
        payload: Mapping[str, Any],
        request_id: str,
        timeout_ms: int = 10_000,
        cancellation: SDKCancellationToken | None = None,
    ) -> dict[str, Any]:
        module = _MODULE_BY_ID.get(str(module_id).strip())
        require(
            module is not None,
            "SDK_MODULE_UNSUPPORTED",
            "The requested SDK authority module is not in the versioned ABI.",
            status="BLOCKED",
            supported=sorted(_MODULE_BY_ID),
        )
        exact_operation = str(operation or "").strip()
        operation_spec = cast(SDKModuleSpec, module).operation(exact_operation)
        require(
            operation_spec is not None,
            "SDK_OPERATION_UNSUPPORTED",
            "The requested operation is not part of this SDK module contract.",
            status="BLOCKED",
            module_id=cast(SDKModuleSpec, module).module_id,
        )
        require(
            isinstance(payload, Mapping),
            "SDK_PAYLOAD_INVALID",
            "An SDK payload must be one bounded object.",
            status="BLOCKED",
        )
        exact_payload = dict(payload)
        encoded_payload = canonical_json_bytes(exact_payload)
        require(
            len(encoded_payload) <= INTERNAL_SDK_MAX_PAYLOAD_BYTES,
            "SDK_PAYLOAD_TOO_LARGE",
            "The SDK payload exceeded the bounded request size.",
            status="BLOCKED",
            max_bytes=INTERNAL_SDK_MAX_PAYLOAD_BYTES,
        )
        secret_paths = _secret_paths(exact_payload)
        require(
            not secret_paths,
            "SDK_SECRET_MATERIAL_FORBIDDEN",
            "Secrets and credentials cannot enter SDK payload or replay state.",
            status="BLOCKED",
            secret_paths=secret_paths,
        )
        require(
            isinstance(timeout_ms, int)
            and 1 <= timeout_ms <= INTERNAL_SDK_MAX_TIMEOUT_MS,
            "SDK_TIMEOUT_INVALID",
            "The SDK timeout must be a positive bounded number of milliseconds.",
            status="BLOCKED",
            max_timeout_ms=INTERNAL_SDK_MAX_TIMEOUT_MS,
        )
        exact_request_id = self._request_id(request_id)
        exact_binding = self._binding(binding)
        self._validate_binding(exact_binding)
        available = self.adapter.available_operations().get(
            cast(SDKModuleSpec, module).module_id, set()
        )
        require(
            exact_operation in available,
            "HOST_CAPABILITY_UNAVAILABLE",
            "The selected provider does not implement this SDK operation.",
            status="UNAVAILABLE",
            adapter_id=self.adapter.adapter_id,
            module_id=cast(SDKModuleSpec, module).module_id,
            operation=exact_operation,
        )
        if cast(SDKOperationSpec, operation_spec).effect != "READ":
            grant = f"{cast(SDKModuleSpec, module).module_id}:{exact_operation}"
            require(
                grant in exact_binding.write_scope,
                "SDK_WRITE_SCOPE_REQUIRED",
                "The exact SDK write operation is outside the bound write scope.",
                status="BLOCKED",
                required_grant=grant,
            )
        request_body = {
            "schema": INTERNAL_SDK_REPLAY_SCHEMA,
            "abi": INTERNAL_SDK_ABI,
            "adapter_id": self.adapter.adapter_id,
            "module_id": cast(SDKModuleSpec, module).module_id,
            "namespace": cast(SDKModuleSpec, module).namespace,
            "operation": exact_operation,
            "binding": exact_binding.as_dict(),
            "payload": exact_payload,
            "request_id": exact_request_id,
        }
        request_sha256 = sha256_bytes(canonical_json_bytes(request_body))
        connection = self._connect(cast(SDKModuleSpec, module))
        try:
            existing = connection.execute(
                "SELECT * FROM sdk_replay WHERE request_id=?", (exact_request_id,)
            ).fetchone()
            if existing is not None:
                require(
                    str(existing["request_sha256"]) == request_sha256
                    and str(existing["binding_sha256"]) == exact_binding.sha256,
                    "SDK_REPLAY_CONFLICT",
                    "This SDK request ID is already bound to different immutable input.",
                    status="MISMATCH",
                    request_id=exact_request_id,
                )
                response = cast(
                    dict[str, Any], json.loads(str(existing["response_json"]))
                )
                require(
                    sha256_bytes(canonical_json_bytes(response))
                    == str(existing["response_sha256"]),
                    "SDK_REPLAY_LEDGER_HASH_MISMATCH",
                    "The SDK replay response failed its immutable hash check.",
                    status="FAIL",
                )
                return {**response, "replay": "IDEMPOTENT_REUSE"}
        finally:
            connection.close()

        token = cancellation or SDKCancellationToken()
        token.require_active()
        started = time.monotonic()
        context = SDKInvocationContext(
            request_id=exact_request_id,
            timeout_ms=timeout_ms,
            started_monotonic=started,
            cancellation=token,
        )
        outcome: dict[str, Any] = {}
        failure: list[BaseException] = []

        def run() -> None:
            try:
                context.checkpoint()
                result = self.adapter.invoke(
                    cast(SDKModuleSpec, module).module_id,
                    exact_operation,
                    exact_binding,
                    exact_payload,
                    context,
                )
                context.checkpoint()
                require(
                    isinstance(result, dict),
                    "SDK_ADAPTER_RESULT_INVALID",
                    "An SDK provider adapter must return one result object.",
                    status="FAIL",
                )
                outcome.update(result)
            except BaseException as exc:  # noqa: BLE001
                failure.append(exc)

        worker = threading.Thread(
            target=run,
            name=f"evidence-lane-sdk-{cast(SDKModuleSpec, module).module_id}",
            daemon=True,
        )
        worker.start()
        worker.join(timeout_ms / 1000)
        if worker.is_alive():
            token.cancel()
            raise EvidenceLaneError(
                "SDK_INVOCATION_TIMEOUT",
                "The provider did not finish inside the bounded SDK timeout.",
                status="TIMEOUT",
                details={
                    "timeout_ms": timeout_ms,
                    "cooperative_cancellation_requested": True,
                },
            )
        if failure:
            error = failure[0]
            if isinstance(error, EvidenceLaneError):
                raise error
            raise EvidenceLaneError(
                "SDK_ADAPTER_FAILURE",
                "The provider adapter failed inside the governed SDK boundary.",
                status="FAIL",
                details={"error_type": type(error).__name__},
            ) from error
        context.checkpoint()
        secret_result_paths = _secret_paths(outcome, "result")
        require(
            not secret_result_paths,
            "SDK_ADAPTER_SECRET_RESULT_BLOCKED",
            "The provider returned secret material that cannot enter SDK replay state.",
            status="BLOCKED",
            secret_paths=secret_result_paths,
        )
        effects = self._validate_effects(
            cast(SDKModuleSpec, module),
            cast(SDKOperationSpec, operation_spec),
            outcome,
        )
        response_body = {
            "schema": INTERNAL_SDK_RESPONSE_SCHEMA,
            "abi": INTERNAL_SDK_ABI,
            "status": str(outcome.get("status") or "PASS"),
            "adapter_id": self.adapter.adapter_id,
            "module_id": cast(SDKModuleSpec, module).module_id,
            "namespace": cast(SDKModuleSpec, module).namespace,
            "authority": cast(SDKModuleSpec, module).authority,
            "operation": exact_operation,
            "request_id": exact_request_id,
            "request_sha256": request_sha256,
            "binding_sha256": exact_binding.sha256,
            "authority_effects": effects,
            "data": {
                key: value
                for key, value in outcome.items()
                if key != "authority_effects"
            },
            "authority_merge_allowed": False,
            "private_reasoning_stored": False,
            "replay": "RECORDED",
        }
        receipt_sha256 = sha256_bytes(canonical_json_bytes(response_body))
        response = {**response_body, "receipt_sha256": receipt_sha256}
        response_sha256 = sha256_bytes(canonical_json_bytes(response))
        connection = self._connect(cast(SDKModuleSpec, module))
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM sdk_replay WHERE request_id=?", (exact_request_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO sdk_replay(
                        request_id,request_sha256,binding_sha256,
                        response_sha256,response_json
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        exact_request_id,
                        request_sha256,
                        exact_binding.sha256,
                        response_sha256,
                        canonical_json_bytes(response).decode("utf-8"),
                    ),
                )
                connection.commit()
            else:
                connection.rollback()
                require(
                    str(existing["request_sha256"]) == request_sha256,
                    "SDK_REPLAY_CONFLICT",
                    "Concurrent SDK calls reused one request ID for other input.",
                    status="MISMATCH",
                )
                stored = cast(
                    dict[str, Any], json.loads(str(existing["response_json"]))
                )
                return {**stored, "replay": "IDEMPOTENT_REUSE"}
        finally:
            connection.close()
        return response

    def retrieve_separately(
        self,
        *,
        binding: SDKBinding | Mapping[str, Any],
        project_truth_payload: Mapping[str, Any],
        learning_payload: Mapping[str, Any],
        request_prefix: str,
        timeout_ms: int = 10_000,
    ) -> dict[str, Any]:
        """Return two named slices; never concatenate or re-rank their hits."""

        prefix = self._request_id(request_prefix)
        truth = self.invoke(
            module_id="project_truth",
            operation="search",
            binding=binding,
            payload=project_truth_payload,
            request_id=f"{prefix}:project-truth",
            timeout_ms=timeout_ms,
        )
        learning = self.invoke(
            module_id="agent_learning",
            operation="retrieve",
            binding=binding,
            payload=learning_payload,
            request_id=f"{prefix}:agent-learning",
            timeout_ms=timeout_ms,
        )
        body = {
            "schema": "evidence-lane.internal-sdk-separate-retrieval.v1",
            "status": "PASS",
            "project_id": self._binding(binding).project_id,
            "project_truth_slice": truth,
            "agent_learning_slice": learning,
            "canon_input_slice": None,
            "host_entry_slice": None,
            "combined_ranking": False,
            "authority_merge_allowed": False,
            "brain_scaling": "BOUNDED_INDEXED_SLICING_NOT_TRAINING",
        }
        return {**body, "receipt_sha256": sha256_bytes(canonical_json_bytes(body))}


def build_local_service_adapter(
    service: Any,
    *,
    runtime_binding: Mapping[str, Any],
    canon_dispatcher: CanonTaskDispatcher | None = None,
) -> RegisteredSDKAdapter:
    """Bind the private SDK to the in-process Evidence Lane service.

    ``runtime_binding`` must come from the host entry/runtime classifier.  The
    adapter overwrites pointer, task, lineage, and ENV/UOP fields with current
    local authority before every call, so a stale host binding fails closed.
    """

    declared_runtime = SDKBinding.from_dict(runtime_binding)

    def snapshot(binding: SDKBinding) -> Mapping[str, Any]:
        project_id = declared_runtime.project_id
        session_id = declared_runtime.session_id
        pointer = service.store.pointer(project_id)
        backlog = service.store.backlog_status(project_id)
        active = [
            row
            for row in backlog["goal_projection"]["rows"]
            if row["status"] == "in_progress"
        ]
        require(
            len(active) == 1,
            "SDK_ACTIVE_PLAN_ROW_REQUIRED",
            "The local SDK adapter requires one active native Plan row.",
            status="MISMATCH",
            active_count=len(active),
        )
        service.sessions.load(project_id, session_id)
        lineage_path = (
            service.store.project_root(project_id)
            / "lineage"
            / f"{session_id}.jsonl"
        )
        events = ChatLineage(lineage_path).events()
        lineage_head = (
            str(events[-1]["event_sha256"])
            if events
            else sha256_bytes(
                canonical_json_bytes(
                    {
                        "project_id": project_id,
                        "session_id": session_id,
                        "state": "NO_LINEAGE_EVENTS",
                    }
                )
            )
        )
        env_uop = derive_host_entry_env_uop(service.flash_authority.status())
        result = declared_runtime.as_dict()
        result.update(
            {
                "project_id": project_id,
                "session_id": session_id,
                "task_id": str(active[0]["task_id"]),
                "accepted_pv": pointer.accepted_pv,
                "pointer_generation": pointer.generation,
                "accepted_manifest_sha256": pointer.accepted_manifest_sha256,
                "lineage_head_sha256": lineage_head,
                **env_uop,
                "write_scope": list(declared_runtime.write_scope),
            }
        )
        return result

    def _truth_status(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return service.status_window(binding.project_id)

    def _truth_search(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return service.reader.search(binding.project_id, **payload)

    def _truth_fetch(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return service.reader.fetch(binding.project_id, **payload)

    def _truth_query(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return service.reader.query(binding.project_id, **payload)

    def _truth_diff(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return service.reader.diff(binding.project_id, **payload)

    def _canon_payload(binding: SDKBinding, payload: dict[str, Any]) -> dict[str, Any]:
        exact = dict(payload)
        supplied_project = exact.pop("project_id", binding.project_id)
        require(
            supplied_project == binding.project_id,
            "SDK_CANON_PROJECT_BINDING_MISMATCH",
            "The Canon SDK payload cannot override its exact project binding.",
            status="BLOCKED",
        )
        require(
            "project_root" not in exact,
            "SDK_CANON_ROOT_OVERRIDE_BLOCKED",
            "The Canon SDK payload cannot override the bound project authority root.",
            status="BLOCKED",
        )
        return exact

    def _canon_root(binding: SDKBinding) -> Path:
        return service.store.project_root(binding.project_id)

    def _canon_inspect(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        _canon_payload(binding, payload)
        return inspect_canon_authority(
            _canon_root(binding), project_id=binding.project_id
        )

    def _canon_inbox(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return inspect_canon_inbox(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_graph(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        _canon_payload(binding, payload)
        return inspect_canon_task_graph(
            _canon_root(binding), project_id=binding.project_id
        )

    def _canon_register_contract(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return register_expected_canon_contract(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_seal(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return seal_canon_envelope(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_receive(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return receive_canon_envelope(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_classify(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return classify_canon_envelope(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_decide(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return decide_canon_input(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_supersede(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return supersede_canon_input(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_register_edge(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return register_canon_task_edge(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_bind_edge(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return bind_received_canon_task_edge(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_dispatch(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return dispatch_linked_canon_task(
            _canon_root(binding),
            project_id=binding.project_id,
            dispatcher=canon_dispatcher,
            **_canon_payload(binding, payload),
        )

    def _canon_backfire(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return raise_canon_backfire(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_seal_result(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return seal_canon_task_result(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_seal_continuity(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return seal_canon_state_travel_continuity(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _canon_restore_continuity(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return restore_canon_state_travel_continuity(
            _canon_root(binding),
            project_id=binding.project_id,
            **_canon_payload(binding, payload),
        )

    def _learning_inspect(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return inspect_learning_authority(
            service.store.project_root(binding.project_id),
            project_id=binding.project_id,
        )

    def _learning_payload(
        binding: SDKBinding, payload: dict[str, Any]
    ) -> dict[str, Any]:
        exact = dict(payload)
        supplied_project = exact.pop("project_id", binding.project_id)
        require(
            supplied_project == binding.project_id,
            "SDK_LEARNING_PROJECT_BINDING_MISMATCH",
            "The Learning SDK payload cannot override its exact project binding.",
            status="BLOCKED",
        )
        require(
            "project_root" not in exact,
            "SDK_LEARNING_ROOT_OVERRIDE_BLOCKED",
            "The Learning SDK payload cannot override the bound project authority root.",
            status="BLOCKED",
        )
        return exact

    def _learning_retrieve(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return retrieve_accepted_learning(
            service.store.project_root(binding.project_id),
            project_id=binding.project_id,
            **_learning_payload(binding, payload),
        )

    def _learning_seal_candidate(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return seal_learning_candidate(
            service.store.project_root(binding.project_id),
            project_id=binding.project_id,
            **_learning_payload(binding, payload),
        )

    def _learning_decide_candidate(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return decide_learning_candidate(
            service.store.project_root(binding.project_id),
            project_id=binding.project_id,
            **_learning_payload(binding, payload),
        )

    def _learning_revoke(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return revoke_learning_candidate(
            service.store.project_root(binding.project_id),
            project_id=binding.project_id,
            **_learning_payload(binding, payload),
        )

    def _lineage(binding: SDKBinding) -> ChatLineage:
        return ChatLineage(
            service.store.project_root(binding.project_id)
            / "lineage"
            / f"{binding.session_id}.jsonl"
        )

    def _lineage_status(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return _lineage(binding).projection_status()

    def _lineage_events(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        limit = int(payload.get("limit", 50))
        require(
            1 <= limit <= 200,
            "SDK_LINEAGE_LIMIT_INVALID",
            "Lineage reads are bounded to 1..200 events.",
            status="BLOCKED",
        )
        events = _lineage(binding).events()[-limit:]
        return {
            "status": "PASS",
            "result": "HIT" if events else "NO_HIT",
            "events": events,
        }

    def _lineage_append(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        allowed = {
            "event_type",
            "visible_payload",
            "occurred_at",
            "session_id",
            "task_id",
            "run_id",
            "event_id",
            "actor_type",
            "model",
            "submodel",
            "token_metrics",
        }
        require(
            set(payload) <= allowed,
            "SDK_LINEAGE_APPEND_FIELD_UNSUPPORTED",
            "Lineage append accepts only the exact visible event fields.",
            status="BLOCKED",
            fields=sorted(set(payload) - allowed),
        )
        supplied_session = str(payload.get("session_id") or binding.session_id)
        supplied_task = str(payload.get("task_id") or binding.task_id)
        supplied_model = str(payload.get("model") or binding.model)
        supplied_submodel = str(payload.get("submodel") or binding.submodel)
        require(
            supplied_session == binding.session_id
            and supplied_task == binding.task_id
            and supplied_model == binding.model
            and supplied_submodel == binding.submodel,
            "SDK_LINEAGE_APPEND_BINDING_MISMATCH",
            "A lineage event cannot override its SDK session, task, or model binding.",
            status="BLOCKED",
        )
        visible_payload = payload.get("visible_payload")
        require(
            isinstance(visible_payload, dict),
            "SDK_LINEAGE_VISIBLE_PAYLOAD_INVALID",
            "A lineage append requires one visible payload object.",
            status="BLOCKED",
        )
        exact_visible_payload = cast(dict[str, Any], visible_payload)
        result = _lineage(binding).append(
            event_type=_exact_text(payload.get("event_type"), field="event_type"),
            visible_payload=dict(exact_visible_payload),
            occurred_at=_exact_text(payload.get("occurred_at"), field="occurred_at"),
            session_id=binding.session_id,
            task_id=binding.task_id,
            run_id=(str(payload["run_id"]) if payload.get("run_id") else None),
            event_id=(
                str(payload["event_id"]) if payload.get("event_id") else None
            ),
            actor_type=(
                str(payload["actor_type"])
                if payload.get("actor_type")
                else "CODEX"
            ),
            model=binding.model,
            submodel=binding.submodel,
            token_metrics=(
                dict(payload["token_metrics"])
                if isinstance(payload.get("token_metrics"), dict)
                else None
            ),
        )
        return {
            **result,
            "authority_effects": {
                "project_truth": "NONE",
                "canon_input": "NONE",
                "agent_learning": "NONE",
                "chat_lineage": "APPENDED",
                "host_entry_continuity": "NONE",
            },
        }

    def _host_entry(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return inspect_host_entry_continuity(
            service.store.project_root(binding.project_id),
            project_id=binding.project_id,
        )

    def _runtime(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return {
            "status": "PASS",
            "doctor": service.doctor(),
            "runtime_activation": service.runtime_activation_status(),
        }

    def _plan(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        exact = dict(payload)
        supplied_project = exact.pop("project_id", binding.project_id)
        require(
            supplied_project == binding.project_id,
            "SDK_PLAN_PROJECT_BINDING_MISMATCH",
            "A Plan SDK read cannot override its exact project binding.",
            status="BLOCKED",
        )
        return service.task_backlog_window(binding.project_id, **exact)

    def _plan_transition(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        exact = dict(payload)
        supplied_project = exact.pop("project_id", binding.project_id)
        task_id = str(exact.get("task_id") or binding.task_id)
        require(
            supplied_project == binding.project_id and task_id == binding.task_id,
            "SDK_PLAN_TRANSITION_BINDING_MISMATCH",
            "An SDK Plan transition is bound to its exact project and active task.",
            status="BLOCKED",
        )
        exact["task_id"] = task_id
        return service.transition_task(binding.project_id, **exact)

    def _source_intake(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        exact = dict(payload)
        supplied_project = exact.pop("project_id", binding.project_id)
        supplied_session = exact.pop("session_id", binding.session_id)
        require(
            supplied_project == binding.project_id
            and supplied_session == binding.session_id,
            "SDK_SOURCE_INTAKE_BINDING_MISMATCH",
            "Source Intake cannot override its SDK project or session binding.",
            status="BLOCKED",
        )
        return service.source_intake(
            binding.project_id,
            session_id=binding.session_id,
            **exact,
        )

    def _env(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return service.session_flash_status()

    def _classify_mode(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        exact = dict(payload)
        supplied_project = exact.pop("project_id", binding.project_id)
        supplied_session = exact.pop("session_id", binding.session_id)
        require(
            supplied_project == binding.project_id
            and supplied_session == binding.session_id,
            "SDK_MODE_BINDING_MISMATCH",
            "Mode classification cannot override its SDK project or session binding.",
            status="BLOCKED",
        )
        result = service.classify_mode(
            binding.project_id,
            session_id=binding.session_id,
            **exact,
        )
        return {
            **result,
            "authority_effects": {
                "project_truth": "NONE",
                "canon_input": "NONE",
                "agent_learning": "NONE",
                "chat_lineage": "APPENDED",
                "host_entry_continuity": "NONE",
            },
        }

    def _storage(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return service.storage_connector_inspect(binding.project_id, **payload)

    def _storage_select(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        exact = dict(payload)
        supplied_project = exact.pop("project_id", binding.project_id)
        require(
            supplied_project == binding.project_id,
            "SDK_STORAGE_PROJECT_BINDING_MISMATCH",
            "Storage selection cannot override its SDK project binding.",
            status="BLOCKED",
        )
        return service.storage_connector_select(binding.project_id, **exact)

    def _hil_payload(
        binding: SDKBinding, payload: dict[str, Any]
    ) -> dict[str, Any]:
        exact = dict(payload)
        supplied_project = exact.pop("project_id", binding.project_id)
        supplied_session = exact.pop("session_id", binding.session_id)
        require(
            supplied_project == binding.project_id
            and supplied_session == binding.session_id,
            "SDK_HIL_BINDING_MISMATCH",
            "A HIL SDK operation cannot override its project or session binding.",
            status="BLOCKED",
        )
        return exact

    def _hil_record_decision(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return service.record_hil_decision(
            binding.project_id,
            binding.session_id,
            **_hil_payload(binding, payload),
        )

    def _hil_fuse(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        result = service.fuse(
            binding.project_id,
            binding.session_id,
            **_hil_payload(binding, payload),
        )
        return {
            **result,
            "authority_effects": {
                "project_truth": "FUSED",
                "canon_input": "NONE",
                "agent_learning": "NONE",
                "chat_lineage": "NONE",
                "host_entry_continuity": "NONE",
            },
        }

    def _hil_rollback(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        result = service.rollback(
            binding.project_id,
            binding.session_id,
            **_hil_payload(binding, payload),
        )
        return {
            **result,
            "authority_effects": {
                "project_truth": "ROLLED_BACK",
                "canon_input": "NONE",
                "agent_learning": "NONE",
                "chat_lineage": "NONE",
                "host_entry_continuity": "NONE",
            },
        }

    def _capabilities(
        binding: SDKBinding, payload: dict[str, Any], context: SDKInvocationContext
    ) -> dict[str, Any]:
        context.checkpoint()
        return {
            "status": "PASS",
            "adapter_id": "evidence-lane.local-service.v1",
            "host_kind": binding.host_kind,
            "headless_supported": False,
            "headless_provider_adapter_id": SDK_EXTERNAL_PROVIDER_OPERATIONS[
                ("provider_host_adapters", "invoke_headless")
            ],
        }

    handlers: dict[tuple[str, str], SDKHandler] = {
        ("project_truth", "status"): _truth_status,
        ("project_truth", "search"): _truth_search,
        ("project_truth", "fetch"): _truth_fetch,
        ("project_truth", "query"): _truth_query,
        ("project_truth", "diff"): _truth_diff,
        ("canon_input", "inspect"): _canon_inspect,
        ("canon_input", "inbox"): _canon_inbox,
        ("canon_input", "graph"): _canon_graph,
        ("canon_input", "register_contract"): _canon_register_contract,
        ("canon_input", "seal_envelope"): _canon_seal,
        ("canon_input", "receive"): _canon_receive,
        ("canon_input", "classify"): _canon_classify,
        ("canon_input", "decide"): _canon_decide,
        ("canon_input", "supersede"): _canon_supersede,
        ("canon_input", "register_edge"): _canon_register_edge,
        ("canon_input", "bind_edge"): _canon_bind_edge,
        ("canon_input", "dispatch_linked_task"): _canon_dispatch,
        ("canon_input", "backfire_hil"): _canon_backfire,
        ("canon_input", "seal_result"): _canon_seal_result,
        ("canon_input", "seal_continuity"): _canon_seal_continuity,
        ("canon_input", "restore_continuity"): _canon_restore_continuity,
        ("agent_learning", "inspect"): _learning_inspect,
        ("agent_learning", "retrieve"): _learning_retrieve,
        ("agent_learning", "seal_candidate"): _learning_seal_candidate,
        ("agent_learning", "decide_candidate"): _learning_decide_candidate,
        ("agent_learning", "revoke"): _learning_revoke,
        ("chat_lineage", "status"): _lineage_status,
        ("chat_lineage", "events"): _lineage_events,
        ("chat_lineage", "append"): _lineage_append,
        ("host_entry_continuity", "inspect"): _host_entry,
        ("lifecycle_hooks", "doctor"): lambda binding, payload, context: (
            service.doctor()
        ),
        ("lifecycle_hooks", "transition_law"): lambda binding, payload, context: (
            service.transition_law()
        ),
        ("lifecycle_hooks", "runtime_status"): _runtime,
        ("plan_delta_tasks", "status"): _plan,
        ("plan_delta_tasks", "backlog"): _plan,
        ("plan_delta_tasks", "transition_task"): _plan_transition,
        ("source_lane_retrieval", "search"): _truth_search,
        ("source_lane_retrieval", "fetch"): _truth_fetch,
        ("source_lane_retrieval", "query"): _truth_query,
        ("source_lane_retrieval", "source_intake"): _source_intake,
        ("env_uop_operator_runtime", "status"): _env,
        ("env_uop_operator_runtime", "classify_mode"): _classify_mode,
        ("storage_connectors", "inspect"): _storage,
        ("storage_connectors", "select"): _storage_select,
        ("storage_connectors", "plugin_catalog"): lambda binding, payload, context: (
            service.connector_plugin_catalog(binding.project_id)
        ),
        ("storage_connectors", "plugin_route"): lambda binding, payload, context: (
            service.connector_plugin_route(binding.project_id, **payload)
        ),
        ("hil_candidate_pointer", "status"): _truth_status,
        ("hil_candidate_pointer", "render_hil"): lambda binding, payload, context: (
            service.prompt_index_status(binding.project_id, binding.session_id)
        ),
        ("hil_candidate_pointer", "record_decision"): _hil_record_decision,
        ("hil_candidate_pointer", "fuse"): _hil_fuse,
        ("hil_candidate_pointer", "rollback"): _hil_rollback,
        ("provider_host_adapters", "capabilities"): _capabilities,
        (
            "provider_host_adapters",
            "binding_status",
        ): lambda binding, payload, context: {
            "status": "PASS",
            "binding": snapshot(binding),
        },
    }
    adapter = RegisteredSDKAdapter(
        adapter_id="evidence-lane.local-service.v1",
        snapshot_provider=snapshot,
        handlers=handlers,
    )
    inspect_sdk_handler_parity(adapter, construction_profile="LOCAL_SERVICE")
    return adapter


def build_live_local_sdk_context(
    service: Any,
    *,
    project_id: str,
    session_id: str,
    write_scope: tuple[str, ...] = (),
    canon_dispatcher: CanonTaskDispatcher | None = None,
) -> tuple[InternalEvidenceLaneSDK, SDKBinding]:
    """Derive one exact live binding and its in-process SDK adapter.

    Public MCP actions use this bridge so the typed action name, current
    accepted pointer, active native Plan row, ENV/UOP identities, lineage head,
    host task, and execution profile are checked together before an SDK arm is
    invoked.  Callers cannot supply or weaken those identities.
    """

    session = service.sessions.load(project_id, session_id)
    pointer = service.store.pointer(project_id)
    backlog = service.store.backlog_status(project_id)
    active = [
        row
        for row in backlog["goal_projection"]["rows"]
        if row["status"] == "in_progress"
    ]
    require(
        len(active) == 1,
        "SDK_ACTIVE_PLAN_ROW_REQUIRED",
        "The live SDK bridge requires exactly one active native Plan row.",
        status="MISMATCH",
        active_count=len(active),
    )
    lineage_path = (
        service.store.project_root(project_id) / "lineage" / f"{session_id}.jsonl"
    )
    events = ChatLineage(lineage_path).events()
    lineage_head = (
        str(events[-1]["event_sha256"])
        if events
        else sha256_bytes(
            canonical_json_bytes(
                {
                    "project_id": project_id,
                    "session_id": session_id,
                    "state": "NO_LINEAGE_EVENTS",
                }
            )
        )
    )
    profile_value = session.metadata.get("execution_profile")
    profile = dict(profile_value) if isinstance(profile_value, Mapping) else {}
    required_profile = {
        "model",
        "submodel",
        "reasoning_effort",
        "reasoning_speed",
    }
    require(
        required_profile <= set(profile)
        and all(str(profile[field] or "").strip() for field in required_profile),
        "SDK_EXECUTION_PROFILE_REQUIRED",
        "The live SDK bridge requires the exact governed execution profile.",
        status="MISMATCH",
        missing=sorted(required_profile - set(profile)),
    )
    host_session_id = str(
        session.metadata.get("current_host_session_id") or ""
    ).strip()
    require(
        bool(host_session_id),
        "SDK_HOST_SESSION_BINDING_REQUIRED",
        "The live SDK bridge requires the exact governed host-session identity.",
        status="MISMATCH",
    )
    env_uop = derive_host_entry_env_uop(service.flash_authority.status())
    binding = SDKBinding.from_dict(
        {
            "project_id": project_id,
            "session_id": session_id,
            "task_id": str(active[0]["task_id"]),
            "accepted_pv": pointer.accepted_pv,
            "pointer_generation": pointer.generation,
            "accepted_manifest_sha256": pointer.accepted_manifest_sha256,
            "lineage_head_sha256": lineage_head,
            **env_uop,
            "model": str(profile["model"]),
            "submodel": str(profile["submodel"]),
            "reasoning_effort": str(profile["reasoning_effort"]),
            "reasoning_speed": str(profile["reasoning_speed"]),
            "host_kind": session.host.value,
            "host_session_id": host_session_id,
            "write_scope": list(write_scope),
        }
    )
    adapter = build_local_service_adapter(
        service,
        runtime_binding=binding.as_dict(),
        canon_dispatcher=canon_dispatcher,
    )
    return (
        InternalEvidenceLaneSDK(service.store.project_root(project_id), adapter),
        binding,
    )

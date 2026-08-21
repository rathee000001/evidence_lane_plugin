"""Deterministic website projection of executable mode/operator contracts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from .errors import require
from .hashing import atomic_write_bytes, canonical_json_bytes, sha256_bytes, sha256_file
from .next_actions import HIL_CHOICES
from .operating_modes import MODE_DEFINITIONS, classify_operating_modes

_CUSTOM_MODE = {
    "name": "evidence choreography",
    "brief": "Demonstrate a user-defined site mode with explicit dependencies.",
    "lanes": ["custom"],
    "dependency_policy": {
        "requires": ["explicit source map", "named validation gate"],
        "on_missing": "BLOCK",
    },
}


def _selection(
    definition: dict[str, Any],
    *,
    selection_kind: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    mode_id = str(definition["id"])
    name = str(definition["name"])
    custom_modes = [_CUSTOM_MODE] if mode_id == "X" else None
    exact_name = str(_CUSTOM_MODE["name"]) if mode_id == "X" else name
    explicit_modes = [exact_name] if selection_kind == "plugin" else None
    request = (
        f"Select {exact_name} through the plugin mode control."
        if selection_kind == "plugin"
        else f"Use {exact_name} mode for this bounded request."
    )
    result = classify_operating_modes(
        request,
        explicit_modes=explicit_modes,
        code_lane="local_code",
        custom_modes=custom_modes,
    )
    selected_modes = cast(list[dict[str, Any]], result["selected_modes"])
    contracts = cast(
        list[dict[str, Any]], result["mode_governance"]["contracts"]
    )
    require(
        len(selected_modes) == 1 and len(contracts) == 1,
        "SITE_MODE_EXPORT_AMBIGUOUS",
        "Each documentation projection must resolve to exactly one mode contract.",
        status="FAIL",
        requested_mode=mode_id,
        selected=[row["id"] for row in selected_modes],
    )
    contract = contracts[0]
    projected = {
        "selection_source": contract["selection_source"],
        "request_sha256": contract["request_sha256"],
        "operator_receipt_sha256": contract["operator_receipt_sha256"],
        "formula_display": contract["formula_display"],
        "formula": contract["formula"],
        "recursive_loop": contract["recursive_loop"],
        "scan_order": contract["scan_order"],
        "unit_of_work": contract["unit_of_work"],
        "validation_gate": contract["validation_gate"],
        "exit_write_target": contract["exit_write_target"],
        "operator_law": contract["operator_law"],
        "operators": contract["operators"],
        "operator_families": contract["operator_families"],
        "ci_cd": contract["ci_cd"],
        "hil": contract["hil"],
        "lifecycle_effect": contract["lifecycle_effect"],
        "candidate_created": contract["candidate_created"],
        "pointer_moved": contract["pointer_moved"],
    }
    identity = {
        "runtime_mode_id": selected_modes[0]["id"],
        "name": selected_modes[0]["name"],
        "routed_lanes": result["canonical_lanes"],
        "env_authority": contract["env_authority"],
    }
    return identity, projected


def build_mode_operator_site_payload() -> dict[str, Any]:
    """Project all built-in modes plus one fail-closed custom-mode example."""

    definitions = [dict(row) for row in MODE_DEFINITIONS]
    modes: list[dict[str, Any]] = []
    for definition in definitions:
        plugin_identity, plugin = _selection(definition, selection_kind="plugin")
        prompt_identity, prompt = _selection(definition, selection_kind="prompt")
        require(
            plugin_identity == prompt_identity,
            "SITE_MODE_EXPORT_IDENTITY_DRIFT",
            "Plugin and prompt selection must resolve to the same mode identity.",
            status="FAIL",
            mode_id=definition["id"],
        )
        require(
            plugin["hil"]["choices"] == prompt["hil"]["choices"],
            "SITE_MODE_EXPORT_HIL_DRIFT",
            "Selection origin cannot change lane-specific HIL semantics.",
            status="FAIL",
            mode_id=definition["id"],
        )
        modes.append(
            {
                "id": definition["id"],
                **plugin_identity,
                "variants": {"plugin": plugin, "prompt": prompt},
            }
        )

    code = next(row for row in modes if row["id"] == "CD")
    require(
        code["variants"]["plugin"]["ci_cd"]["required"] is True
        and code["variants"]["plugin"]["formula"]["rule"]
        == "plan -> sandbox build -> test -> hash -> package",
        "SITE_CODE_MODE_CONTRACT_INVALID",
        "The website cannot publish Code mode without its exact CI/CD formula.",
        status="FAIL",
    )
    for mode in modes:
        choices = mode["variants"]["plugin"]["hil"]["choices"]
        require(
            [choice["token"] for choice in choices] == list(HIL_CHOICES),
            "SITE_MODE_HIL_VOCABULARY_INVALID",
            "Every site mode must preserve the universal six exact HIL tokens.",
            status="FAIL",
            mode_id=mode["id"],
        )

    source_root = Path(__file__).resolve().parent
    body = {
        "schema": "evidence-lane.site-mode-operator-guide.v1",
        "mode_count": len(modes),
        "selection_variants": ["plugin", "prompt"],
        "six_way_token_vocabulary": list(HIL_CHOICES),
        "universal_boundary": (
            "The six tokens are exact and universal; accepted objects, gates, "
            "rollback targets, and effects are mode-specific. Mode selection is "
            "never HIL approval."
        ),
        "source_files": {
            "mode_governance.py": sha256_file(source_root / "mode_governance.py"),
            "operating_modes.py": sha256_file(source_root / "operating_modes.py"),
        },
        "modes": modes,
    }
    body["export_sha256"] = sha256_bytes(canonical_json_bytes(body))
    return body


def write_mode_operator_site_payload(path: str | Path) -> dict[str, Any]:
    payload = build_mode_operator_site_payload()
    rendered = (
        __import__("json").dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    atomic_write_bytes(path, rendered)
    return payload

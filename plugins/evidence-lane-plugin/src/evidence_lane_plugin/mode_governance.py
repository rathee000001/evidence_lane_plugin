"""ENV/UOP-backed operator contracts for selected operating modes.

The projection below is a package-local executable contract derived from sealed
ENV15 tables.  It intentionally distinguishes exact ENV rows from derived mode
semantics and never treats mode selection as HIL approval or lifecycle mutation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from .errors import require
from .hashing import canonical_json_bytes, sha256_bytes
from .next_actions import HIL_CHOICES

ENV15_ENV_SQLITE_SHA256 = (
    "78EEC5EFF7BA82DF38DF62ED65F2E8A4B8E1F3A593B8387779EAD7EA45E03810"
)
ENV15_UOP_SQLITE_SHA256 = (
    "DB2539AAC36BE38D89C74D052C4764ECD28E4CFA29EFBF4EB6B0E4234CB1377F"
)
ENV15_MODE_POLICY_PROJECTION_SHA256 = (
    "F66B383EFF37DE7550D24A00821F7F34257E26152FAD4C4A6B09166A8EB67AF1"
)

_POLICIES: dict[str, dict[str, Any]] = {
    "D": {
        "policy_name": "Discussion",
        "authority": "lane_recursive_policy_v7:LANE_DISCUSSION",
        "scan_order": ["discussion_cluster", "accepted_deltas", "current_prompt"],
        "unit_of_work": "prompt topic",
        "recursive_loop": "entry -> discuss -> delta/log -> exit",
        "validation_gate": "no code/files unless user changes mode",
        "exit_write_target": "turn_event + visible_reasoning_lane",
        "operator_law": "Relations/Functions + Mathematical Reasoning",
        "formula_rule": "prompt -> package state -> UOP guidance -> answer -> receipt",
        "formula_authority": "lane_formula_execution_registry_v12:LANE_D",
        "ci_cd_required": False,
        "accepted_object": "visible discussion record and accepted delta/log",
        "rollback_target": "prior accepted discussion checkpoint",
    },
    "AL": {
        "policy_name": "Analysis",
        "authority": "lane_recursive_policy_v7:LANE_ANALYSIS",
        "scan_order": [
            "analysis_cluster",
            "source_inventory",
            "related_discussion",
            "accepted_deltas",
        ],
        "unit_of_work": "source/gap/failure unit",
        "recursive_loop": "entry -> inspect -> classify -> findings -> exit",
        "validation_gate": "source truth + no fake claims",
        "exit_write_target": "analysis cluster + gate_evaluation_run",
        "operator_law": "Sets + Statistics + Probability",
        "formula_rule": "prompt -> sources -> relational map -> findings -> receipt",
        "formula_authority": "lane_formula_execution_registry_v12:LANE_AL",
        "ci_cd_required": False,
        "accepted_object": "source-backed findings and gate evaluation",
        "rollback_target": "prior accepted analysis receipt",
    },
    "PL": {
        "policy_name": "Planning",
        "authority": "lane_recursive_policy_v7:LANE_PLANNING",
        "scan_order": [
            "planning_cluster",
            "discussion_route",
            "source_constraints",
            "open_deltas",
        ],
        "unit_of_work": "phase/schema unit",
        "recursive_loop": "entry -> plan -> simulate -> hold/open -> exit",
        "validation_gate": "no build until user says pass/code",
        "exit_write_target": "planning cluster + delta_ledger",
        "operator_law": "Linear Programming + Operations Strategy",
        "formula_rule": "entry -> plan -> simulate -> hold/open -> exit",
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "plan and simulation state without code mutation",
        "rollback_target": "prior accepted plan checkpoint",
    },
    "CD": {
        "policy_name": "Code",
        "authority": "lane_recursive_policy_v7:LANE_CODE",
        "scan_order": [
            "discussion_cluster",
            "planning_cluster",
            "code_cluster",
            "validation_cluster",
            "error_open_deltas",
            "source_files",
        ],
        "unit_of_work": "phase/subphase/code patch",
        "recursive_loop": "entry -> preflight -> sandbox -> patch -> test -> exit",
        "validation_gate": ("safe build + redox/organic reaction + handoff on fail"),
        "exit_write_target": "code cluster + artifacts + validation",
        "operator_law": ("Redox + Organic reaction pathways + Environmental chemistry"),
        "formula_rule": "plan -> sandbox build -> test -> hash -> package",
        "formula_authority": "lane_formula_execution_registry_v12:LANE_CD",
        "ci_cd_required": True,
        "accepted_object": "tested package hash and executable CI/CD receipts",
        "rollback_target": "exact prior accepted PV or code checkpoint",
    },
    "XL": {
        "policy_name": "Excel",
        "authority": "lane_recursive_policy_v7:LANE_EXCEL",
        "scan_order": [
            "discussion_cluster",
            "workbook_plan",
            "sheet_cluster",
            "formula_dependency_graph",
            "validation",
        ],
        "unit_of_work": "sheet/table/formula block",
        "recursive_loop": (
            "entry -> sheet plan -> formula build -> validate cells -> exit"
        ),
        "validation_gate": "formula lineage + no hidden sheet drift",
        "exit_write_target": "artifact_chunk + formula matrix",
        "operator_law": "Matrices + Relations + Accounting",
        "formula_rule": (
            "entry -> sheet plan -> formula build -> validate cells -> exit"
        ),
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "workbook artifact, formula lineage, and cell validation",
        "rollback_target": "prior accepted workbook/formula checkpoint",
    },
    "PPT": {
        "policy_name": "PowerPoint",
        "authority": "lane_recursive_policy_v7:LANE_PPT",
        "scan_order": [
            "discussion_cluster",
            "story_plan",
            "slide_cluster",
            "visual_proof",
            "speaker_logic",
        ],
        "unit_of_work": "slide/story unit",
        "recursive_loop": (
            "entry -> slide outline -> visual/source validation -> exit"
        ),
        "validation_gate": "public/private + source proof",
        "exit_write_target": "artifact_registry + slide chunks",
        "operator_law": "Optics + Marketing/Branding + OB",
        "formula_rule": ("entry -> slide outline -> visual/source validation -> exit"),
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "deck, slide/source proof, and public-safe validation",
        "rollback_target": "prior accepted deck checkpoint",
    },
    "DOC": {
        "policy_name": "Document",
        "authority": "lane_recursive_policy_v7:LANE_DOCX",
        "scan_order": [
            "discussion_cluster",
            "doc_purpose",
            "source_truth",
            "style_structure",
            "render_QA",
        ],
        "unit_of_work": "section/table/page",
        "recursive_loop": "entry -> section build -> render QA -> exit",
        "validation_gate": "render/QA and source-backed text",
        "exit_write_target": "doc artifact + render receipt",
        "operator_law": "Surface chemistry + Communication systems",
        "formula_rule": "entry -> section build -> render QA -> exit",
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "document artifact, source map, and render receipt",
        "rollback_target": "prior accepted document checkpoint",
    },
    "JD": {
        "policy_name": "JD Matching",
        "authority": "lane_recursive_policy_v7:LANE_JD",
        "scan_order": [
            "jd_parse_cluster",
            "project_evidence_cluster",
            "direct_adjacent_ramp",
            "public_safe_gate",
        ],
        "unit_of_work": "JD requirement row",
        "recursive_loop": (
            "entry -> parse -> match -> classify direct/adjacent/ramp -> exit"
        ),
        "validation_gate": "no fake direct proof",
        "exit_write_target": "jd_match_fact + proof cards",
        "operator_law": "Sets + Probability + Decision Support",
        "formula_rule": (
            "entry -> parse -> match -> classify direct/adjacent/ramp -> exit"
        ),
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "requirement classifications and evidence proof cards",
        "rollback_target": "prior accepted JD match matrix",
    },
    "PB": {
        "policy_name": "Project Brain Builder",
        "authority": (
            "lane_formula_execution_registry_v12:LANE_PB + "
            "lane_recursive_policy_v7:LANE_SQLITE"
        ),
        "scan_order": ["root_packet", "schema", "migration", "hash_chain", "mmd_sync"],
        "unit_of_work": "schema/table/migration",
        "recursive_loop": (
            "entry -> schema preflight -> sandbox -> migrate -> hash -> exit"
        ),
        "validation_gate": "write receipt + mmd after sqlite",
        "exit_write_target": "env_sqlite_write_receipt + mmd_node_registry",
        "operator_law": "Matrices + Determinants + Database/ERP",
        "formula_rule": (
            "register source -> inventory -> index -> graph -> project sqlite -> project MMD"
        ),
        "formula_authority": "lane_formula_execution_registry_v12:LANE_PB",
        "ci_cd_required": True,
        "accepted_object": "project SQLite, project MMD, hashes, and build receipts",
        "rollback_target": "prior accepted brain/package checkpoint",
    },
    "RS": {
        "policy_name": "Research",
        "authority": "lane_recursive_policy_v7:LANE_RESEARCH",
        "scan_order": [
            "question",
            "source_stack",
            "internet_context_if_allowed",
            "source_truth_split",
        ],
        "unit_of_work": "research claim section",
        "recursive_loop": (
            "entry -> source rank -> external context -> conclusion -> exit"
        ),
        "validation_gate": "internet context-only unless asked",
        "exit_write_target": "research cluster + citations summary",
        "operator_law": "Statistics + Source precedence",
        "formula_rule": (
            "entry -> source rank -> external context -> conclusion -> exit"
        ),
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "source-ranked conclusion and citation summary",
        "rollback_target": "prior accepted research checkpoint",
    },
    "X": {
        "policy_name": "Custom User Mode",
        "authority": "lane_recursive_policy_v7:LANE_CUSTOM",
        "scan_order": ["user_defined_mode", "cluster_policy", "gates", "exit_write"],
        "unit_of_work": "user-defined unit",
        "recursive_loop": "entry -> create lane -> apply route -> exit",
        "validation_gate": "must define dependency policy",
        "exit_write_target": "custom mode cluster",
        "operator_law": "User-defined operator trigger",
        "formula_rule": "entry -> create lane -> apply route -> exit",
        "formula_authority": "lane_recursive_policy_v7:recursive_loop",
        "ci_cd_required": False,
        "accepted_object": "user-defined deliverable under its dependency policy",
        "rollback_target": "prior accepted custom-mode checkpoint",
    },
}

_EXTRA_POLICIES: dict[str, dict[str, Any]] = {
    "OP": {
        "policy_name": "Output",
        "authority": "mode_cluster:output + mode_cluster_dependency:output",
        "scan_order": ["OUTPUT_CLUSTER"],
        "unit_of_work": "artifact response",
        "recursive_loop": "gates pass -> artifact response -> lineage receipt",
        "validation_gate": "artifact response only after gates pass",
        "exit_write_target": "artifact output lane",
        "operator_law": "Optics + Accounting",
        "formula_rule": "gates pass -> artifact response -> lineage receipt",
        "formula_authority": "DERIVED_FROM_ENV_MODE_CLUSTER_PURPOSE",
        "ci_cd_required": False,
        "accepted_object": "source-bound artifact and render lineage",
        "rollback_target": "prior accepted artifact checkpoint",
    },
    "VAL": {
        "policy_name": "Validation",
        "authority": "mode_cluster:validation + mode_cluster_dependency:validation",
        "scan_order": ["VALIDATION_CLUSTER"],
        "unit_of_work": "validation gate",
        "recursive_loop": "inspect -> classify PASS/WARN/OPEN/BLOCKED/FAIL -> receipt",
        "validation_gate": "every claimed gate needs executable evidence",
        "exit_write_target": "validation receipt set",
        "operator_law": "Mathematical Reasoning + Statistics + Accounting",
        "formula_rule": ("inspect -> classify PASS/WARN/OPEN/BLOCKED/FAIL -> receipt"),
        "formula_authority": "DERIVED_FROM_ENV_MODE_CLUSTER_PURPOSE",
        "ci_cd_required": False,
        "accepted_object": "executable validation receipt set",
        "rollback_target": "prior accepted validation checkpoint",
    },
    "ENG": {
        "policy_name": "Project Engulf",
        "authority": "mode_namespace_registry:ENG + project_engulf sector law",
        "scan_order": ["project package", "source registry", "topology", "gates"],
        "unit_of_work": "project source package",
        "recursive_loop": "inspect -> explicit grant -> controlled fusion -> receipt/relock",
        "validation_gate": "explicit one-turn mutation grant and immediate relock",
        "exit_write_target": "project_engulf sector + mutation receipt",
        "operator_law": "Redox + Organic pathway + Management Information Systems",
        "formula_rule": (
            "inspect -> explicit grant -> controlled fusion -> receipt/relock"
        ),
        "formula_authority": "DERIVED_FROM_MODE_NAMESPACE_AND_SECTOR_LAW",
        "ci_cd_required": False,
        "accepted_object": "registered project package and topology-growth receipt",
        "rollback_target": "prior accepted project-engulf checkpoint",
    },
    "CE": {
        "policy_name": "Clean Exit",
        "authority": "mode_namespace_registry:CE + chat_lineage append-only law",
        "scan_order": ["exact prompt", "exact files", "visible response", "receipts"],
        "unit_of_work": "Entry/Exit Slip continuity packet",
        "recursive_loop": "PREPARE -> visible response -> COMMIT -> receipt",
        "validation_gate": "hidden reasoning excluded; append-only head monotonic",
        "exit_write_target": "chat_lineage + Exit Slip",
        "operator_law": "Communication Systems + Mathematical Reasoning + Human gates",
        "formula_rule": "PREPARE -> visible response -> COMMIT -> receipt",
        "formula_authority": "CHAT_LINEAGE_APPEND_ONLY_LAW",
        "ci_cd_required": False,
        "accepted_object": "exact Exit Slip and continuity packet",
        "rollback_target": "prior accepted continuity checkpoint",
    },
    "RCV": {
        "policy_name": "Recovery",
        "authority": "mode_namespace_registry:RCV",
        "scan_order": ["preserved state", "receipts", "pointers", "recovery artifacts"],
        "unit_of_work": "exact recovery boundary",
        "recursive_loop": "resume preserved state -> verify receipts -> return to boundary",
        "validation_gate": "no fabricated State Travel or pointer mutation",
        "exit_write_target": "recovery receipt + chat lineage",
        "operator_law": "Continuity + fallback paths + Risk and Crisis Management",
        "formula_rule": (
            "resume preserved state -> verify receipts -> return to boundary"
        ),
        "formula_authority": "DERIVED_FROM_ENV_RECOVERY_NAMESPACE",
        "ci_cd_required": False,
        "accepted_object": "verified exact-resume boundary and recovery receipts",
        "rollback_target": "exact prior accepted pointer named by the user",
    },
}

_OPERATORS: dict[int, dict[str, Any]] = {
    4: {"family": "PHYSICS", "chapter": "Laws of Motion", "effect": "no route drift"},
    6: {
        "family": "PHYSICS",
        "chapter": "System of Particles and Rotational Motion",
        "effect": "rollback pivots",
    },
    13: {
        "family": "PHYSICS",
        "chapter": "EMI and Alternating Current",
        "effect": "fallback paths",
    },
    15: {
        "family": "PHYSICS",
        "chapter": "Optics",
        "effect": "public/private visibility",
    },
    19: {
        "family": "PHYSICS",
        "chapter": "Communication Systems",
        "effect": "channel encoding and feedback",
    },
    27: {
        "family": "CHEMISTRY",
        "chapter": "Redox Reactions",
        "effect": "sandbox-first patch risk",
    },
    28: {
        "family": "CHEMISTRY",
        "chapter": "Organic Chemistry Principles",
        "effect": "phase patch pathway",
    },
    29: {
        "family": "CHEMISTRY",
        "chapter": "Environmental Chemistry",
        "effect": "dependency and leak safety",
    },
    34: {
        "family": "CHEMISTRY",
        "chapter": "Surface Chemistry",
        "effect": "rendered surface versus backend truth",
    },
    42: {"family": "MATHS", "chapter": "Sets", "effect": "source and claim sets"},
    43: {
        "family": "MATHS",
        "chapter": "Relations and Functions",
        "effect": "typed source-output mapping",
    },
    55: {
        "family": "MATHS",
        "chapter": "Mathematical Reasoning",
        "effect": "contradiction and proof gates",
    },
    56: {
        "family": "MATHS",
        "chapter": "Statistics",
        "effect": "coverage and error distribution",
    },
    57: {
        "family": "MATHS",
        "chapter": "Probability",
        "effect": "uncertainty and risk thresholds",
    },
    58: {
        "family": "MATHS",
        "chapter": "Relations and Functions",
        "effect": "reverse typed mappings",
    },
    60: {
        "family": "MATHS",
        "chapter": "Matrices",
        "effect": "source-gate-mode matrices",
    },
    61: {
        "family": "MATHS",
        "chapter": "Determinants",
        "effect": "critical gate viability",
    },
    62: {
        "family": "MATHS",
        "chapter": "Continuity and Differentiability",
        "effect": "smooth phase lineage",
    },
    69: {
        "family": "MATHS",
        "chapter": "Linear Programming",
        "effect": "proof/risk constraint optimization",
    },
    71: {
        "family": "MBA",
        "chapter": "Accounting",
        "effect": "audit and source ledgers",
    },
    74: {
        "family": "MBA",
        "chapter": "Management Information Systems",
        "effect": "system integration and information flow",
    },
    76: {
        "family": "MBA",
        "chapter": "Production and Operations Management",
        "effect": "process and bottleneck control",
    },
    80: {
        "family": "MBA",
        "chapter": "Organizational Behavior",
        "effect": "human gates",
    },
    81: {
        "family": "MBA",
        "chapter": "Strategic Marketing and Branding",
        "effect": "public-safe positioning",
    },
    82: {
        "family": "MBA",
        "chapter": "Operations and Supply Chain",
        "effect": "workflow stages and data logistics",
    },
    90: {
        "family": "MBA",
        "chapter": "Database Management and ERP",
        "effect": "database ledgers",
    },
    98: {
        "family": "MBA",
        "chapter": "Decision Support Systems",
        "effect": "evidence-backed decision output",
    },
    106: {
        "family": "SUPPLY",
        "chapter": "Risk and Crisis Management",
        "effect": "failure handoff and rollback",
    },
    110: {
        "family": "SUPPLY",
        "chapter": "Operations Strategy",
        "effect": "technical route alignment",
    },
}

_MODE_OPERATORS: dict[str, tuple[int, ...]] = {
    "D": (43, 55),
    "AL": (42, 56, 57),
    "PL": (69, 110),
    "CD": (4, 6, 27, 28, 29, 55, 58, 61, 71, 74, 76, 80, 82, 106),
    "XL": (43, 60, 71),
    "PPT": (15, 80, 81),
    "DOC": (19, 34),
    "JD": (42, 57, 98),
    "PB": (58, 60, 61, 74, 90),
    "RS": (56, 71),
    "X": (),
    "OP": (15, 71),
    "VAL": (55, 56, 71),
    "ENG": (27, 28, 29, 74),
    "CE": (19, 55, 80),
    "RCV": (6, 13, 62, 106),
}


def _hil_contract(mode_id: str, policy: Mapping[str, Any]) -> dict[str, Any]:
    accepted_object = str(policy["accepted_object"])
    validation_gate = str(policy["validation_gate"])
    rollback_target = str(policy["rollback_target"])
    code_like = mode_id in {"CD", "PB"}
    choices = [
        {
            "token": "APPROVE",
            "lane_effect": (
                f"Accept {accepted_object}. "
                + (
                    "Candidate promotion remains an exact lifecycle HIL action."
                    if code_like
                    else "This does not authorize code, deployment, or pointer movement."
                )
            ),
            "requires": "all mode-specific validation gates PASS",
        },
        {
            "token": "APPROVE_WITH_DELTA",
            "lane_effect": (
                f"Open one bounded {policy['policy_name']} correction and rerun "
                f"{policy['recursive_loop']}."
            ),
            "requires": "one exact correction",
        },
        {
            "token": "MORE_RESEARCH",
            "lane_effect": (
                f"Hold {accepted_object}; answer one bounded source or evidence gap."
            ),
            "requires": "one exact research question",
        },
        {
            "token": "ROLLBACK",
            "lane_effect": f"Return only to {rollback_target}.",
            "requires": "one exact accepted target when the target is not implicit",
        },
        {
            "token": "REJECT",
            "lane_effect": f"Reject {accepted_object} without accepting its state.",
            "requires": "one visible reason",
        },
        {
            "token": "FAIL",
            "lane_effect": f"Record failure of gate: {validation_gate}.",
            "requires": "one exact failed gate or executable receipt",
        },
    ]
    require(
        [row["token"] for row in choices] == list(HIL_CHOICES),
        "MODE_HIL_TOKEN_CONTRACT_MISMATCH",
        "Mode HIL semantics must preserve the universal six exact tokens.",
        status="FAIL",
    )
    return {
        "schema": "evidence-lane.mode-hil-semantics.v1",
        "authority": (
            "GLOBAL_EXACT_TOKENS_PLUS_ENV_LANE_GATE_LOOP_AND_EXIT_TARGET_DERIVATION"
        ),
        "mode_id": mode_id,
        "accepted_object": accepted_object,
        "choices": choices,
        "mode_selection_is_not_hil_approval": True,
        "implicit_promotion_allowed": False,
    }


def _compile_custom_dependency_policy(
    selected_mode: Mapping[str, Any],
) -> dict[str, Any]:
    schema = selected_mode.get("schema")
    require(
        isinstance(schema, dict),
        "CUSTOM_MODE_SCHEMA_REQUIRED",
        "A selected custom mode needs its explicit session schema.",
        status="BLOCKED",
        mode_id=selected_mode.get("id"),
    )
    schema_dict = cast(dict[str, Any], schema)
    dependency_policy = schema_dict.get("dependency_policy")
    require(
        isinstance(dependency_policy, dict)
        and set(dependency_policy) <= {"on_missing", "requires"}
        and isinstance(dependency_policy.get("requires", []), list)
        and str(dependency_policy.get("on_missing") or "BLOCK").upper() == "BLOCK",
        "CUSTOM_MODE_DEPENDENCY_POLICY_REQUIRED",
        "ENV15 Custom mode requires an explicit dependency list and fail-closed BLOCK policy.",
        status="BLOCKED",
        mode_id=selected_mode.get("id"),
    )
    exact_policy = cast(dict[str, Any], dependency_policy)
    return {
        "requires": exact_policy.get("requires", []),
        "on_missing": "BLOCK",
    }


def govern_mode_selection(
    selected_modes: list[dict[str, Any]],
    *,
    request: str,
    selection_source: str,
) -> dict[str, Any]:
    """Return visible formulas, operator receipts, and lane-specific HIL semantics."""

    request_sha256 = sha256_bytes(request.encode("utf-8"))
    contracts: list[dict[str, Any]] = []
    for selected in selected_modes:
        full_mode_id = str(selected["id"])
        mode_id = "X" if full_mode_id.startswith("X:") else full_mode_id
        policy = dict(_POLICIES.get(mode_id) or _EXTRA_POLICIES[mode_id])
        dependency_policy = None
        if mode_id == "X":
            dependency_policy = _compile_custom_dependency_policy(selected)
            policy["accepted_object"] = (
                f"custom deliverable '{selected['name']}' under its dependency policy"
            )
        operators = [
            {"operator_id": operator_id, **_OPERATORS[operator_id]}
            for operator_id in _MODE_OPERATORS[mode_id]
        ]
        operator_families = list(dict.fromkeys(str(row["family"]) for row in operators))
        ci_cd = {
            "required": bool(policy["ci_cd_required"]),
            "loop": (policy["formula_rule"] if policy["ci_cd_required"] else None),
            "controlled": bool(policy["ci_cd_required"]),
            "autonomous_flash_fuse_deploy_allowed": False,
            "authority": (
                "lane_formula_execution_registry_v12"
                if policy["ci_cd_required"]
                else "MODE_SPECIFIC_VALIDATION_NOT_GENERIC_CI_CD"
            ),
        }
        contract_core = {
            "schema": "evidence-lane.mode-governance-contract.v1",
            "mode_id": full_mode_id,
            "mode_name": selected["name"],
            "selection_source": selection_source,
            "request_sha256": request_sha256,
            "canonical_lanes": selected["canonical_lanes"],
            "env_authority": {
                "env_sqlite_sha256": ENV15_ENV_SQLITE_SHA256,
                "uop_sqlite_sha256": ENV15_UOP_SQLITE_SHA256,
                "mode_policy_projection_sha256": ENV15_MODE_POLICY_PROJECTION_SHA256,
                "policy_row": policy["authority"],
            },
            "scan_order": policy["scan_order"],
            "unit_of_work": policy["unit_of_work"],
            "recursive_loop": policy["recursive_loop"],
            "validation_gate": policy["validation_gate"],
            "exit_write_target": policy["exit_write_target"],
            "operator_law": policy["operator_law"],
            "formula": {
                "rule": policy["formula_rule"],
                "authority": policy["formula_authority"],
                "visible_in_response": True,
            },
            "operators": operators,
            "operator_families": operator_families,
            "ci_cd": ci_cd,
            "dependency_policy": dependency_policy,
            "hil": _hil_contract(mode_id, policy),
            "lifecycle_effect": "NONE",
            "candidate_created": False,
            "pointer_moved": False,
        }
        receipt_sha256 = sha256_bytes(canonical_json_bytes(contract_core))
        formula_display = (
            f"Mode={selected['name']} | ENV formula: {policy['formula_rule']} | "
            f"Loop: {policy['recursive_loop']} | CI/CD: "
            f"{'CONTROLLED_REQUIRED' if ci_cd['required'] else 'NOT_GENERIC_TO_THIS_MODE'} | "
            f"Operators: {', '.join(operator_families) if operator_families else policy['operator_law']} | "
            f"Receipt={receipt_sha256}"
        )
        contracts.append(
            {
                **contract_core,
                "operator_receipt_sha256": receipt_sha256,
                "formula_display": formula_display,
            }
        )

    receipt_projection = [
        {
            "mode_id": row["mode_id"],
            "operator_receipt_sha256": row["operator_receipt_sha256"],
        }
        for row in contracts
    ]
    combined_receipt_sha256 = sha256_bytes(canonical_json_bytes(receipt_projection))
    return {
        "schema": "evidence-lane.mode-governance-selection.v1",
        "status": "PASS",
        "selection_source": selection_source,
        "request_sha256": request_sha256,
        "contracts": contracts,
        "visible_formula_response": [row["formula_display"] for row in contracts],
        "combined_operator_receipt_sha256": combined_receipt_sha256,
        "six_way_hil_is_lane_specific": True,
        "six_way_token_vocabulary_preserved": list(HIL_CHOICES),
        "mode_selection_is_not_hil_approval": True,
        "lifecycle_effect": "NONE",
        "candidate_created": False,
        "pointer_moved": False,
    }

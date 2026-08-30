"""Source-derived Markdown renderers for maintained Evidence Lane pages."""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent
from typing import Any

TECHNICAL_PAGES = {
    "ARCHITECTURE.md",
    "docs/AI_LEARNING.md",
    "docs/CANON_TASK_GRAPH_AND_INPUT_HIL.md",
    "docs/CODEX_V300_LOCAL_INSTALL_AND_RELOAD.md",
    "docs/GIT_AND_CI_CD.md",
    "docs/HOOKS.md",
    "docs/HOST_AND_STORAGE_MATRIX.md",
    "docs/LIFECYCLE_AND_HIL.md",
    "docs/MCP.md",
    "docs/MEMORY.md",
    "docs/PLAN_AND_CHANGE_DISPLAY.md",
    "docs/PROJECT_PV_CONTENT_ADDRESSED_STORAGE.md",
    "docs/PROJECT_UNIVERSE.md",
    "docs/RELEASE_AND_COMPATIBILITY.md",
    "docs/REPOSITORY_MAP.md",
    "docs/SKILLS.md",
    "docs/SOURCE_INTAKE_AND_LANES.md",
    "docs/TOOLS.md",
    "docs/UPSTREAM_REFERENCE_PROVENANCE.md",
    "docs/USER_TUNNEL_GUIDE.md",
}

LANE_PURPOSE = {
    "github_code": "Git refs, commits, trees, blobs, changes, and repository history.",
    "local_code": "Working-tree files, code structure, chunks, and dependencies.",
    "chat_lineage": "Prompts, responses, steers, and task entry/exit evidence.",
    "discussion": "Bounded discussion claims and decisions.",
    "analysis": "Source-backed findings, relationships, and uncertainty.",
    "plan": "Canonical Plan rows, dependencies, transitions, and projections.",
    "mode": "Operating-mode classification and intersection.",
    "docs": "Documentation hierarchy, text, relationships, and citations.",
    "data_excel": "Tabular and spreadsheet structure, formulas, and typed facts.",
    "ppt": "Slides, notes, shapes, tables, and media references.",
    "pdf_ocr": "PDF structure, native text, page geometry, and OCR fallback.",
    "images_ocr": "Image metadata, OCR, and visual locators.",
    "artifacts": "Generated deliverables and exact artifact identities.",
    "custom": "Explicit user-defined source schemas.",
    "brain_loader": "Imported Evidence Lane/SQLite brain packages.",
    "research": "Web and research evidence, citations, and provenance.",
    "project_engulf": "Initial project classification and lane registration plan.",
    "sqlite_brain": "Existing SQLite schema, relationships, and bounded queries.",
}

AUTHORITY_PURPOSE = {
    "agent_learning": "Project-isolated learning candidates, decisions, accepted lessons, and revocations.",
    "canon_input": "Typed task contracts, envelopes, receiver decisions, edges, and results.",
    "project_memory": "Bounded memory locators and typed cross-authority links.",
    "project_overlay": "Full-PV proposal overlay at the owning Project HIL only.",
    "source_authority": "Exact source identities, occurrences, provenance, and source graph.",
    "project_universe": "Per-project relationship graph.",
    "connector_brain": "Connector grants and hash-only federated mini-brain links.",
    "project_authority": "Project root, layout, membership, pointer, and registration.",
    "receipt_ledger": "Exact input, route, result, provenance, and linkage receipts.",
    "session_authority": "Session, host, attachment, State Travel, and Goal continuity.",
    "instructions": "AGENTS.md and host MEMORY.md instruction chain.",
}


PAGE_FLOW_SPECS: dict[str, dict[str, Any]] = {
    "ARCHITECTURE.md": {
        "nodes": ("Authorized prompt or source", "Entry Slip and Source Intake", "Typed action and internal SDK", "ENV then UOP", "Authority, lane, and selected tools", "Hooks and effect validation", "Content-addressed receipt", "Delta exit, HIL, or Exit Slip", "Fail closed with no authority effect"),
        "sources": ("toolchains/universal-plugin-architecture.v1.json", "sdk/sdk-manifest.v1.json", "authorities/authority-surface-registry.v1.json"),
    },
    "docs/AI_LEARNING.md": {
        "nodes": ("Evidence-backed lesson proposal", "Learning candidate classification", "Agent Learning authority", "Separate Learning HIL", "Accept, reject, or research", "Evidence and scope validation", "Learning decision receipt", "Bounded later retrieval or revocation", "Keep Project Truth unchanged"),
        "sources": ("authorities/agent_learning/manifest.v1.json", "skills/evi-learning/SKILL.md", "schemas/actions/learning_seal_candidate.schema.json"),
    },
    "docs/CANON_TASK_GRAPH_AND_INPUT_HIL.md": {
        "nodes": ("Typed cross-task requirement or result", "Envelope and contract classification", "Receiver-owned Canon authority", "Canon Input HIL", "Bind edge, backfire, or return", "Cycle, expiry, and schema validation", "Canon continuity receipt", "Exact linked-task continuation", "Reject ambiguity without merging tasks"),
        "sources": ("authorities/canon_input/manifest.v1.json", "authorities/canon_input/consequence_graph/manifest.v1.json", "skills/evi-canon/SKILL.md"),
    },
    "docs/CODEX_V300_LOCAL_INSTALL_AND_RELOAD.md": {
        "nodes": ("Reviewed exact package bytes", "Plugin Creator validation", "Local-testing selector", "Hidden runtime profile", "Prewarm and installed smoke", "Catalog and member parity", "Install and restart receipts", "Exact app and task reload", "No Project/PV or HIL effect"),
        "sources": (".codex-plugin/plugin.json", "scripts/codex_release/install_codex_stable.py", "manifests/package/package-surface-coherence.json"),
    },
    "docs/GIT_AND_CI_CD.md": {
        "nodes": ("Preserved worktree and dirty bytes", "Reviewed staging allowlist", "Feature-branch Git index", "Required CI workflows", "Exact commit and branch preview", "Checks and fingerprint validation", "Git and CI receipts", "Explicit merge or release decision", "No implicit HIL, merge, or deployment"),
        "sources": ("schemas/github-app-manifest.schema.json", "src/evidence_lane_plugin/remote_git.py", "scripts/codex_release/push_github_app_exact_commit.py"),
    },
    "docs/HOOKS.md": {
        "nodes": ("Native Codex host event", "Event and timing classification", "One of 11 hook classes", "Four ordered handlers", "Validate, emit, transport, and seal", "Trust and invocation proof", "Hook receipt", "Bounded lifecycle strengthening", "Disable only the failing untrusted event"),
        "sources": ("hooks/hook-event-registry.v1.json", "hooks/hooks.json", "schemas/hooks/hook-runtime.v1.json"),
    },
    "docs/HOST_AND_STORAGE_MATRIX.md": {
        "nodes": ("Observed Codex host", "Lifetime and capability classification", "ENV host profile", "Durable storage selection", "Local, mounted, or connector route", "Runtime attestation and integrity", "Host and storage receipt", "Boot/resume eligibility", "Fail closed without durable authority"),
        "sources": ("env/authority-manifest.v1.json", "schemas/install/local-install.v1.json", "src/evidence_lane_plugin/storage_connector.py"),
    },
    "docs/LIFECYCLE_AND_HIL.md": {
        "nodes": ("Prompt, steer, or carried task", "Entry Slip and Delta entry", "Active Plan row", "Bounded execution and refresh", "Candidate or continuing work", "Authority-owned validation", "Delta, HIL, or Exit receipt", "Fuse, rollback, State Travel, or Goal", "Never infer approval from execution"),
        "sources": ("schemas/lifecycle/runtime-workflow-registry.v1.json", "skills/evidence-lane-code-lifecycle/SKILL.md", "schemas/fuse/dual-hil-fuse.v1.json"),
    },
    "docs/MCP.md": {
        "nodes": ("Host-visible action request", "Public schema validation", "One of 91 MCP actions", "Outer-to-internal SDK route", "Owning module operation", "Effect and annotation validation", "Structured MCP receipt", "Bounded result to Codex", "No alias, prefix rewrite, or borrowed authority"),
        "sources": ("mcp/mcp-manifest.v1.json", "schemas/public-action-schemas.v001.json", "sdk/sdk-manifest.v1.json"),
    },
    "docs/MEMORY.md": {
        "nodes": ("Visible project or task evidence", "Owner-specific memory classification", "ChatLineage, Memory, Learning, Canon, or Universe", "SQLite and bounded indexes", "Exact locator query", "Authority and provenance validation", "Memory query receipt", "Bounded context projection", "Never merge memory authorities"),
        "sources": ("authorities/project_memory/manifest.v1.json", "schemas/memory/project-memory.v1.sql", "skills/evi-memory/SKILL.md"),
    },
    "docs/PLAN_AND_CHANGE_DISPLAY.md": {
        "nodes": ("Accepted Plan or execution-changing steer", "Stable row and Delta classification", "Canonical Plan SQLite", "Exactly one active row", "Host Step Task List projection", "Order, state, and receipt validation", "Plan projection receipt", "Rehydrate or advance exact row", "Never replace authority with UI summary"),
        "sources": ("schemas/plan/project-bootstrap.v1.json", "src/evidence_lane_plugin/plan_runtime.py", "skills/evi-plan/SKILL.md"),
    },
    "docs/PROJECT_PV_CONTENT_ADDRESSED_STORAGE.md": {
        "nodes": ("Authorized source bytes", "SHA-256 identity and chunking", "Project/PV authority root", "SQLite, FTS, graph, and derived views", "Atomic candidate generation", "Integrity and manifest validation", "PV and storage receipts", "Unaccepted candidate or accepted pointer", "Never rewrite immutable accepted bytes"),
        "sources": ("schemas/lane-artifact-contract.v001.json", "src/evidence_lane_plugin/store.py", "src/evidence_lane_plugin/project_overlay.py"),
    },
    "docs/PROJECT_UNIVERSE.md": {
        "nodes": ("Project-owned relationships", "Typed edge classification", "Project Universe authority", "Hash-only mini-brain projection", "Optional Bigger Universe link", "Project, grant, and provenance validation", "Universe receipt", "Bounded relationship query", "Never merge Project Truth across projects"),
        "sources": ("authorities/project_universe/manifest.v1.json", "schemas/universe/project-universe.v1.sql", "skills/evi-bigger-universe/SKILL.md"),
    },
    "docs/RELEASE_AND_COMPATIBILITY.md": {
        "nodes": ("Reviewed source and release intent", "Version and compatibility classification", "Exact package and Git identity", "Branch CI and installed proof", "Main-slot release candidate", "Release authority validation", "Release receipts", "Explicit promotion and readback", "Historical receipts stay non-executable"),
        "sources": (".codex-plugin/plugin.json", "release-channels.json", "scripts/codex_release/accept_codex_stable.py"),
    },
    "docs/REPOSITORY_MAP.md": {
        "nodes": ("Repository member", "Executable, generated, docs, test, or local classification", "Canonical owner directory", "Manifest and source-impact graph", "Package or Git allowlist", "Hash and path-policy validation", "Membership receipt", "Included release member or excluded local byte", "Purge stale duplicate ownership"),
        "sources": ("manifests/executable-surface-registry.v1.json", "manifests/package/source-manifest.json", "src/evidence_lane_plugin/source_disposition.py"),
    },
    "docs/SKILLS.md": {
        "nodes": ("User-selected intent", "Skill routing classification", "One of 26 governed skills", "Ordered MCP workflow groups", "Typed action owner", "Tool existence and result validation", "Skill routing receipt", "Bounded reusable workflow", "Fail closed on missing or ambiguous route"),
        "sources": ("skills/skill-surface-registry.v1.json", "skills/evi/references/mcp-tool-routing.v1.json", "sdk/workflows/skill-workflow-registry.v1.json"),
    },
    "docs/SOURCE_INTAKE_AND_LANES.md": {
        "nodes": ("Authorized source identity", "Entry and source classification", "Project recipe and Mode", "One or more of 18 sector lanes", "Lane-specific parse, SQLite, MMD, and DOT", "Schema, hash, and tool validation", "Lane refresh receipt", "Atomic current generation", "No placeholder lane or universal all-tool run"),
        "sources": ("authorities/project_sectors/lane-surface-registry.v1.json", "schemas/source-intake/source-intake-code-routing.v1.json", "src/evidence_lane_plugin/source_intake.py"),
    },
    "docs/TOOLS.md": {
        "nodes": ("Condition-true capability need", "Tool requirement classification", "One of 119 tool requirements", "Primary and eligible fallback order", "Local, SDK, MCP, tunnel, or external execution", "Availability, license, and result validation", "Tool execution receipt", "Owning action phase", "Never confuse tools with 91 MCP actions"),
        "sources": ("toolchains/tool-requirement-matrix.v1.json", "toolchains/tool-execution-routing.v1.json", "toolchains/tool-license-inventory.v1.json"),
    },
    "docs/USER_TUNNEL_GUIDE.md": {
        "nodes": ("Proven host transport gap", "Tunnel eligibility classification", "Version-bound tunnel identity", "Project-neutral transport route", "Selected action payload", "Runtime, secret, and endpoint validation", "Tunnel receipt", "Return to owning MCP action", "Never become agent, catalog, or project authority"),
        "sources": ("toolchains/tunnel-runtime-toolchain.v1.json", "tunnel/README.md", "src/evidence_lane_plugin/tunnel_identity_routing.py"),
    },
}


def _header(title: str, summary: str) -> list[str]:
    return [
        "<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / registry-derived-v1 -->",
        "",
        f"# {title}",
        "",
        summary,
        "",
        "Current counts are derived release facts, not permanent ceilings.",
        "",
    ]


def _footer() -> list[str]:
    return [
        "",
        "---",
        "",
        "This page is a Git-tracked documentation projection. Executable source, SQLite authorities, installed-runtime receipts, and explicit human gates remain the governing evidence.",
        "",
    ]


def _facts(values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    catalog = values["schemas/public-action-schemas.v001.json"]
    skills = values["skills/skill-surface-registry.v1.json"]
    hooks = values["hooks/hook-event-registry.v1.json"]
    tools = values["toolchains/tool-requirement-matrix.v1.json"]
    licenses = values["toolchains/tool-license-inventory.v1.json"]
    accelerators = values["toolchains/hardware-accelerator-routing.v1.json"]
    routing = values["toolchains/tool-execution-routing.v1.json"]
    architecture = values["toolchains/universal-plugin-architecture.v1.json"]
    lanes = values["authorities/project_sectors/lane-surface-registry.v1.json"]
    authorities = values["authorities/authority-surface-registry.v1.json"]
    sdk = values["sdk/sdk-manifest.v1.json"]
    mcp = values["mcp/mcp-manifest.v1.json"]
    return {
        "catalog": catalog,
        "skills": skills,
        "hooks": hooks,
        "tools": tools,
        "licenses": licenses,
        "accelerators": accelerators,
        "routing": routing,
        "architecture": architecture,
        "lanes": lanes,
        "authorities": authorities,
        "sdk": sdk,
        "mcp": mcp,
    }


def _contract_depth(page: str, facts: dict[str, Any]) -> list[str]:
    """Render the common source-bound depth contract with a page-specific map."""

    spec = PAGE_FLOW_SPECS[page]
    nodes = tuple(str(value).replace('"', "'") for value in spec["nodes"])
    sources = tuple(str(value) for value in spec["sources"])
    counts = facts["architecture"]["counts"]
    lines = [
        "## Source-bound workflow map",
        "",
        "This page is projected from the same current executable snapshot as the rest of the documentation set. The map is deliberately two-directional: each horizontal district shows peer stages while vertical edges show ownership and state progression.",
        "",
        "```mermaid",
        "flowchart TB",
        '    subgraph InputDistrict["Input and classification"]',
        "      direction LR",
        f'      A["{nodes[0]}"] --> B["{nodes[1]}"] --> C["{nodes[2]}"]',
        "    end",
        '    subgraph ExecutionDistrict["Selection and execution"]',
        "      direction TB",
        f'      D["{nodes[3]}"] --> E["{nodes[4]}"] --> F["{nodes[5]}"]',
        "    end",
        '    subgraph EvidenceDistrict["Evidence and outcome"]',
        "      direction LR",
        f'      G["{nodes[6]}"] --> H["{nodes[7]}"]',
        f'      G -. mismatch .-> I["{nodes[8]}"]',
        "    end",
        "    C --> D",
        "    F --> G",
        "```",
        "",
        "## Contract and readback",
        "",
        "| Phase | Current contract | Required readback |",
        "| --- | --- | --- |",
        f"| Input | {nodes[0]} | Exact identity, provenance, and scope |",
        f"| Classification | {nodes[1]} | Owning schema, action, lane, skill, or authority |",
        f"| Owner | {nodes[2]} | One canonical implementation owner |",
        f"| Route | {nodes[3]} | Condition-true ordered route with no hidden alias |",
        f"| Execution | {nodes[4]} | Real execution or a visible fail-closed result |",
        f"| Validation | {nodes[5]} | Hash, schema, authority-effect, and negative-case checks |",
        f"| Receipt | {nodes[6]} | Content-addressed result and provenance receipt |",
        f"| Downstream | {nodes[7]} | Only the explicitly eligible next state |",
        f"| Failure | {nodes[8]} | No inferred HIL, candidate acceptance, or pointer movement |",
        "",
        "## Canonical source owners",
        "",
    ]
    lines.extend(f"- `{source}`" for source in sources)
    lines.extend(
        [
            "",
            "## Cross-surface invariants",
            "",
            f"- The current snapshot contains {counts['public_actions']} public actions, {counts['skills']} skills, {counts['hook_events']} hook events / {counts['hook_handler_actions']} handlers, {counts['tool_requirements']} tool requirements, {counts['sector_lanes']} sector lanes, and {counts['named_root_authorities']} named authorities. These are derived counts, not fixed ceilings.",
            "- Executable ownership stays one-way: skills select, MCP exposes, the outer SDK routes, the internal SDK executes, ENV selects, UOP governs, tools perform bounded work, hooks emit receipts, and the owning authority validates effects.",
            "- Any missing identity, schema, grant, capability, dependency, receipt, or authority proof must fail closed at its owning phase; a later green check cannot retroactively authorize the skipped boundary.",
            "- A changed route refreshes every dependent schema, manifest, generator, test, diagram, and documentation reference; the superseded executable route is directly purged in the same Delta.",
            "- Tests, Git, CI, installation, restart, deployment, discussion, or a rendered page never imply Project HIL, Learning HIL, Goal completion, or pointer movement.",
        ]
    )
    return lines


def _enrich_technical_page(
    page: str,
    rendered: str,
    facts: dict[str, Any],
) -> str:
    footer = "\n".join(_footer()).strip("\n")
    suffix = footer + "\n"
    if not rendered.endswith(suffix):
        raise RuntimeError(f"TECHNICAL_PAGE_FOOTER_MISSING:{page}")
    core = rendered[: -len(suffix)].rstrip()
    return core + "\n\n" + "\n".join(_contract_depth(page, facts)) + "\n\n" + suffix


def _action_rows(facts: dict[str, Any], owners: set[str]) -> list[dict[str, Any]]:
    return [
        row
        for row in facts["catalog"]["tools"]
        if str(row["route_contract"].get("owner_skill")) in owners
    ]


def _action_table(rows: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Action | Access | Internal owner |",
        "| --- | --- | --- |",
    ]
    for row in rows:
        route = dict(row["route_contract"])
        internal = dict(route.get("internal_sdk") or {})
        access = "read" if row["annotations"].get("readOnlyHint") else "write-capable"
        owner = (
            f"`{internal.get('module_id')}:{internal.get('operation')}`"
            if internal
            else "general internal engine route"
        )
        lines.append(f"| `{row['name']}` | {access} | {owner} |")
    return lines


def _lane_table(facts: dict[str, Any]) -> list[str]:
    lines = ["| Lane | Role |", "| --- | --- |"]
    for row in facts["lanes"]["lanes"]:
        lane_id = str(row["lane_id"])
        lines.append(f"| `{lane_id}` | {LANE_PURPOSE[lane_id]} |")
    return lines


def _authority_table(facts: dict[str, Any]) -> list[str]:
    lines = ["| Authority | Role |", "| --- | --- |"]
    for row in facts["authorities"]["authorities"]:
        authority = str(row["authority_id"])
        lines.append(f"| `{authority}` | {AUTHORITY_PURPOSE[authority]} |")
    return lines


def _architecture(facts: dict[str, Any]) -> str:
    architecture = facts["architecture"]
    counts = architecture["counts"]
    lines = _header(
        "Evidence Lane 3.0.0 architecture",
        "Evidence Lane is a registry-driven Codex execution and evidence architecture. It keeps environment selection, governance, project authorities, sector lanes, tools, transports, hooks, results, and human decisions distinct while connecting them through typed receipts.",
    )
    lines.extend(
        [
            "## Plugin-maintainer release cycle and downstream projects",
            "",
            "A downstream user's project PV does not reinstall, cache-bust, restart, promote, or otherwise inherit the plugin-maintainer release cycle. Downstream Project/PV work consumes an already verified installed contract and preserves its own Plan, Goal, HIL, pointer, and accepted-state authorities.",
            "",
            "## Current executable topology",
            "",
            "```mermaid",
            "flowchart TB",
            '    Prompt["Prompt or steer"] --> Slip["Entry Slip"]',
            '    Slip --> Intake["Source Intake + project recipe + Mode"]',
            '    Intake --> Action["Typed action + schema"]',
            '    Action --> SDK["Internal SDK owner"]',
            '    SDK --> ENV["ENV selection"]',
            '    ENV --> UOP["UOP governance"]',
            '    UOP --> Accelerator["Eligible CPU / NVIDIA / AMD execution provider"]',
            '    Accelerator --> Surface["Authority + sector lane"]',
            '    Surface --> Tools["Condition-true tools"]',
            '    Tools --> Transport["Local / outer SDK / MCP / tunnel"]',
            '    Transport --> Hooks["Ordered emitted hooks"]',
            '    Hooks --> Result["Validate + receipt + direct stale-route purge"]',
            '    Result --> Delta["Adaptive Delta-exit append"]',
            '    Result --> Exit["Exit Slip: Goal option 2 or State Travel only"]',
            "```",
            "",
            "| Registry surface | Current value |",
            "| --- | ---: |",
            f"| Public actions | {counts['public_actions']} |",
            f"| Skills | {counts['skills']} |",
            f"| Hook events / handlers | {counts['hook_events']} / {counts['hook_handler_actions']} |",
            f"| Sector lanes | {counts['sector_lanes']} |",
            f"| Named authorities | {counts['named_root_authorities']} |",
            f"| Source modules | {counts['source_modules']} |",
            f"| Schemas | {counts['schema_files']} |",
            f"| Tool requirements | {counts['tool_requirements']} |",
            "",
            "## Routing stages",
            "",
        ]
    )
    lines.extend(
        f"{index}. `{row['stage']}` from `{row['source']}`"
        for index, row in enumerate(architecture["routing_stages"], 1)
    )
    lines.extend(
        [
            "",
            "## ENV and UOP",
            "",
            "ENV is the environment decision authority: host profile, context, locality, availability, grants, Mode, project recipe, and eligible ordered pipeline. UOP is the governance authority: operators, formulas, project-class HIL, work/privacy/disclosure gates, and declared same-class fallback. UOP cannot override ENV, Project Truth, Plan, Goal, or HIL.",
            "",
            "Both are clean Codex-native schema-version-17 SQLite action planes with bounded FTS and MMD/DOT traversal maps. They store no prompt corpus, discussion history, ChatLineage payload, uploaded artifact packet, foreign path, or external-model agent authority.",
            "",
            "ENV selects CPU, NVIDIA CUDA, AMD ROCm, or AMD DirectML only from an explicit user-enabled provider grant and compatible runtime probe. UOP independently enforces action eligibility, telemetry, throttle state, configured VRAM budget, and visible CPU fallback. Accelerators are execution providers, not tools, MCP actions, models, or authorities.",
            "",
            "## Named authorities",
            "",
        ]
    )
    lines.extend(_authority_table(facts))
    lines.extend(["", "## Project-sector lanes", ""])
    lines.extend(_lane_table(facts))
    lines.extend(
        [
            "",
            "## Tool roles",
            "",
            "| Role class | Count | Boundary |",
            "| --- | ---: | --- |",
        ]
    )
    for role, count in architecture["tool_taxonomy"]["counts"].items():
        lines.append(
            f"| `{role}` | {count} | Selected only when the exact action phase makes the condition true |"
        )
    lines.extend(
        [
            "",
            "FastMCP, native/domain MCP, the outer SDK, and the tunnel are transport or composition layers. They never become the acting agent or project authority. OpenAI Agents SDK is a subordinate Codex-owned function-tool/MCP client library only.",
            "",
            "## Storage and refresh",
            "",
            "Project/PV roots own or link project authorities. Source bytes and chunks are content-addressed once; SQLite/FTS indexes, lane facts, and graphs are refreshed atomically; unchanged atoms are reused. A replacement route directly purges superseded executable/schema/generated/test/doc references in the same Delta while immutable receipts remain non-executable history.",
        ]
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _lifecycle(facts: dict[str, Any]) -> str:
    lines = _header(
        "Lifecycle, Delta flow, and human gates",
        "Evidence Lane separates entry classification, bounded work, continuing-work refresh, full-PV proposals, authority-owned human decisions, pointer movement, State Travel, and Goal completion.",
    )
    lines.extend(
        dedent(
            """
            ## Turn and Delta flow

            ```text
            Prompt or steer
                -> Entry Slip
                -> Source Intake / project recipe / Mode
                -> typed action and owning authority
                -> ENV selection
                -> UOP operators, formulas and gates
                -> condition-true tools and transport
                -> validate result and authority effects
                -> adaptive Delta-exit append for continuing work
            ```

            Entry Slip is emitted for every prompt or steer. Adaptive Delta-exit append is not an Exit Slip. Exit Slip is emitted only when State Travel completes or the Goal is explicitly completed through option 2.

            ## Project and Learning HIL

            Build and full-PV Refresh can seal an immutable unaccepted proposal. Project HIL and Learning HIL are separate pending decisions. Plan acceptance, natural language, tests, Git, CI, packaging, installation, restart, deployment, or a website preview cannot satisfy either gate.

            Exact Fuse may move the accepted Project pointer only after the current Project decision contract passes. Learning acceptance moves only its separate Learning pointer. Ordinary Delta refresh never creates or promotes Project Overlay.

            ## Canon Input HIL

            Canon input is receiver-owned and uses the exact current contract for ACCEPT, REJECT, or MORE_RESEARCH. It admits or rejects bounded task input only; it cannot decide Project HIL, Learning HIL, Goal completion, or pointer movement.

            ## Rollback

            Logical rollback moves an accepted pointer among immutable accepted PVs under its exact gate. Hard ZIP restore is a separate explicit recovery operation.

            ## State Travel

            State Travel resumes exact unfinished work in a fresh task after task, workspace/worktree, dirty-byte, source, runtime, Plan/Goal, accepted-pointer, and continuity bindings pass. It does not restart the app, reconstruct the Plan from chat, replay HIL, or infer identity from a title, CWD, PID, or successful test.

            ## Goal completion

            Goal completion is human-owned and independent of every HIL. Only the explicit Goal completion path can close it. Completion authorizes no Project acceptance, pointer movement, Git action, installation, merge, or deployment.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _source_intake(facts: dict[str, Any]) -> str:
    lines = _header(
        "Source Intake and project-sector lanes",
        "Source Intake converts authorized source identities into a project recipe and the exact set of sector-lane workflows required by the user's current intent.",
    )
    lines.extend(
        [
            "## Intake sequence",
            "",
            "1. Bind the prompt/steer Entry Slip and exact source identity.",
            "2. Apply path/content policy, redaction, provenance, and project classification.",
            "3. Compile or reuse the project recipe and Mode intersection.",
            "4. Route each source to the applicable lane; a source can be overridden only through the explicit schema/route contract.",
            "5. Parse and chunk exact bytes once, project lane-specific facts, refresh contentless FTS, and derive MMD/DOT from SQLite.",
            "6. Validate hashes, schemas, conditional tool execution, authority effects, and receipts before atomic pointer swap.",
            "7. Reuse unchanged content-addressed atoms and directly purge superseded unpointed generations after readback.",
            "",
            "## Current lanes",
            "",
        ]
    )
    lines.extend(_lane_table(facts))
    lines.extend(
        dedent(
            """

            ## Lane package contract

            Every current lane owns a distinct SQLite schema/template, lane-specific workflow JSON, MMD, DOT, tools contract, manifest, reader, builder, pointer schema, and refresh receipt. Empty tables are classified as query-time materialization, condition-false, intentionally empty template/history, blocked upstream tool, or defect. Eligibility is not execution proof: every condition-true tool must execute or fail visibly.

            Git history and local working-tree content remain separate lanes. Project Engulf registers/classifies a project and its baseline sources; it does not flatten all project material into ENV/UOP or create acceptance. ChatLineage is a sector source, not the Plan or Project Truth.

            Project recipes and Modes are paired inputs to ENV selection; neither is a fixed workflow list. New project types, lanes, tools, and schema facts may be registered when current source and user intent require them.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _mcp(facts: dict[str, Any]) -> str:
    catalog = facts["catalog"]
    lines = _header(
        "Native MCP and outer routing",
        "Evidence Lane exposes one package-local native MCP identity and pairs every public action one-for-one with its schema, internal SDK route, outer binding, skill workflow, authority effects, conditional tools, hooks, and receipt contract.",
    )
    lines.extend(
        [
            "| Surface | Current value |",
            "| --- | ---: |",
            f"| Canonical actions | {catalog['tool_count']} |",
            f"| Read-only | {catalog['read_tool_count']} |",
            f"| Write-capable | {catalog['write_tool_count']} |",
            f"| SDK action bindings | {facts['sdk']['action_binding_count']} |",
            f"| MCP action bindings | {facts['mcp']['action_binding_count']} |",
            "",
            "The 30-row `SPECIALIZED_NATIVE_ACTIONS` tuple is only the specialized Canon/Learning/Memory/first-class subset (9 reads and 21 writes). It is not the canonical action total.",
            "",
            "## Routing law",
            "",
            "- FastMCP is preferred when the exact action and host support that composition route.",
            "- Native or domain MCP servers are conditional transports for their declared scope.",
            "- The outer SDK selects local versus transport routing; the internal SDK owns execution.",
            "- One project-neutral tunnel may bridge a proven host tool gap; it is not the catalog, scheduler, project registry, or lifecycle owner.",
            "- Missing capability, grant, credential, locality, version, or schema proof fails visibly. No prefix rewrite or hidden alias revives a removed action.",
            "- Public visibility is capability discovery, never permission, HIL, or acceptance.",
            "",
            "## Agent boundary",
            "",
            "Codex is the sole acting agent. OpenAI Agents SDK is a subordinate typed function-tool and MCP client library; it has zero independent action, lane, Plan, Goal, HIL, memory, or project authority. External model-agent planes are not part of this plugin.",
        ]
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _skills(facts: dict[str, Any]) -> str:
    lines = _header(
        "Governed skills",
        "Skills are registry-driven workflow selectors over typed public actions. They do not duplicate execution logic and there is no separate command layer.",
    )
    lines.extend(
        ["| Skill | Current purpose | Workflow groups |", "| --- | --- | ---: |"]
    )
    for row in facts["skills"]["skills"]:
        workflow = dict(row["workflow"])
        lines.append(
            f"| `{row['name']}` | {row['description']} | {len(workflow['ordered_tool_groups'])} |"
        )
    lines.extend(
        [
            "",
            "A first-class action requires a first-class skill/workflow binding. Counts can change with the registry. Missing actions fail closed; no command alias or prompt example creates an executable route.",
        ]
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _hooks(facts: dict[str, Any]) -> str:
    lines = _header(
        "Lifecycle hook events",
        "Hooks are ordered host-event adapters. They improve capture and continuity but do not own business logic, HIL, completion, or pointer movement.",
    )
    lines.extend(["| Event | Order | Handlers |", "| --- | ---: | ---: |"])
    for row in facts["hooks"]["events"]:
        lines.append(
            f"| `{row['event']}` | {row['event_number']} | {row['handler_count']} |"
        )
    lines.extend(
        [
            "",
            "The current registry contains 11 event classes and 44 ordered handlers. Explicit skills and native actions remain available when hooks are disabled for repair. An event runs only when the host emits it, and a failing event can be isolated without granting unrelated hooks authority.",
        ]
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _tools(plugin_root: Path) -> str:
    matrix = (plugin_root / "toolchains/TOOLCHAIN_EXECUTION_MATRIX.md").read_text(
        encoding="utf-8"
    )
    lines = _header(
        "Conditional AI and project toolchain",
        "This page embeds the current generated execution matrix. The public MCP action inventory is separate from the tool-requirement inventory.",
    )
    accelerators = json.loads(
        (plugin_root / "toolchains/hardware-accelerator-routing.v1.json").read_text(
            encoding="utf-8"
        )
    )
    lines.extend(
        [
            "## Hardware acceleration providers",
            "",
            "| Provider | Activation boundary |",
            "| --- | --- |",
            "| `CPU` | Universal deterministic baseline and visible fallback |",
            "| `NVIDIA_CUDA` | User-enabled NVIDIA plugin/grant + compatible NVIDIA hardware, driver, exact CUDA runtime, telemetry, budget, and eligible action |",
            "| `AMD_ROCM` | User-enabled AMD plugin/grant + exact AMD hardware/OS/framework compatibility and HIP telemetry |",
            "| `AMD_DIRECTML` | User-enabled AMD plugin/grant + Windows DirectML provider; sequential execution and disabled memory-pattern optimization |",
            "",
            f"The default GPU memory ceiling is **{accelerators['default_memory_budget_percent']}%** with explicit headroom. It limits admitted VRAM; it never forces a utilization percentage. A missing/incompatible runtime, exhausted budget, thermal/throttle signal, or ineligible action yields a receipted CPU fallback.",
            "",
            "Accelerators are not included in the tool count or MCP action count.",
            "",
        ]
    )
    lines.extend(matrix.splitlines()[1:])
    lines.extend(_footer())
    return "\n".join(lines)


def _learning(facts: dict[str, Any]) -> str:
    lines = _header(
        "AI Agent Learning authority",
        "Agent Learning is a project-isolated authority for evidence-backed procedural, failure-avoidance, relational, tool-routing, and host-compatibility lessons. It never becomes Project Truth.",
    )
    lines.extend(_action_table(_action_rows(facts, {"evi-learning"})))
    lines.extend(
        dedent(
            """

            Candidates remain unaccepted until the separate Learning HIL records the exact decision. Accepted Learning moves only the Learning pointer; revocation is append-only and does not erase historical evidence. Host MEMORY.md can be linked only through an explicit nonauthoritative provenance receipt.

            Learning may inform later work through bounded retrieval. It cannot change a Project pointer, accept a Project proposal, alter Canon, replace Project Memory, or infer HIL from repetition or model confidence.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _canon(facts: dict[str, Any]) -> str:
    lines = _header(
        "Canon task graph and input HIL",
        "Canon coordinates typed evidence, requirements, corrections, plans, and results between exact governed tasks without merging their Project Truth, Learning, Plan, Goal, ownership, or HIL.",
    )
    lines.extend(_action_table(_action_rows(facts, {"evi-canon"})))
    lines.extend(
        dedent(
            """

            Each envelope binds source/destination task identity, direction, schema, expected contract, dependency, expiry, evidence, and result requirements. The receiver owns classification and the three-way Canon Input HIL: ACCEPT, REJECT, or MORE_RESEARCH.

            Linked top-level tasks may own independent HIL. Explicitly authorized subagents may perform bounded work but never own HIL. Backfire is deduplicated and addressed to the task that can supply the missing input. State Travel continuity preserves the graph; it does not execute State Travel or replay decisions.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _memory(facts: dict[str, Any]) -> str:
    lines = _header(
        "Memory and knowledge boundaries",
        "Evidence Lane uses several owner-specific memory surfaces. They cooperate through typed locators and receipts without becoming one generic agent memory.",
    )
    lines.extend(
        dedent(
            """
            | Surface | Retention and role |
            | --- | --- |
            | Current prompt/steer | Short-lived input classified by Entry Slip and Source Intake |
            | ChatLineage | Visible task prompts, responses, steers, events, and entry/exit boundaries |
            | Project Memory | Durable bounded locators and relationships across project authorities |
            | Agent Learning | Accepted reusable lessons under separate Learning HIL |
            | Canon | Typed cross-task contracts, messages, and result continuity |
            | Project Universe | Per-project relationship graph |
            | Instructions | AGENTS.md and host MEMORY.md chain, nonauthoritative to Project Truth |

            SQLite/FTS5/BM25 remains the durable bounded retrieval authority. Optional local semantic indexes can rank candidates but cannot replace canonical IDs or owning SQLite. Raw project databases, whole Markdown histories, or full chat archives are not dumped into model context.

            PreCompact seals the active continuity boundary; PostCompact rehydrates the same bounded Plan/Goal/task/source coordinates. Compaction is not a new PV, decision, or Goal.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _plan() -> str:
    lines = _header(
        "Plan, Goal, Delta, and host task display",
        "The canonical Plan is durable SQLite authority. Host Plan/Goal/task UI is a bounded projection and can be rehydrated without rewriting canonical rows.",
    )
    lines.extend(
        dedent(
            """
            ## Canonical authority

            The Plan ledger owns row identity, order, status, dependencies, steers, Deltas, HIL boundaries, and physically final work. Exactly one executable row is active. Completed, dropped, corrected, and superseded records remain queryable history.

            ## Host projection

            The normal plugin projection is a bounded header plus current executable window; its size is a presentation contract, not the Plan's total size. A temporary maintainer task list can remain authoritative through a release workflow when explicitly locked by the user. Panel loss, restart, compaction, or State Travel triggers rehydration from the same Plan identity rather than a duplicate fallback list.

            ## Steering and Delta law

            A steer appends or explicitly reorders/supersedes work. Every Delta implements the current route and directly purges the executable/schema/generated/test/doc references it replaces. Git appears only on the row where Git actually runs.

            Goal completion is a separate explicit human action. Plan completion, HIL, tests, automation, pauses, and task transitions cannot complete the Goal.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _pv_storage() -> str:
    lines = _header(
        "Project/PV content-addressed storage",
        "A user-selected Project/PV root is the durable project baseline. It keeps accepted versions, proposals, authorities, sectors, pointers, and receipts physically separate from the installed plugin runtime and task workspace.",
    )
    lines.extend(
        dedent(
            """
            Exact source and chunk bytes are stored once by SHA-256 and reused across refreshes. SQLite remains canonical; MMD/DOT, vector indexes, renderings, and summaries are derived traversal/query surfaces. Atomic generation swap occurs only after schema, foreign-key, integrity, hash, and tool receipts pass.

            A full Project Version is immutable. The accepted pointer moves only through its governed decision. Unaccepted candidates and Project Overlay remain outside accepted truth. Logical rollback moves the pointer; it does not rewrite historical PV bytes.

            Project registration records the hidden plugin runtime/control root, the external Project/PV root, and the task workspace as distinct identities. ENV/UOP remains hidden runtime state and is not copied into every project folder.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _universe(facts: dict[str, Any]) -> str:
    lines = _header(
        "Project Universe and Bigger Universe",
        "Project Universe is the relationship graph inside one project. Bigger Universe is a separate federation of explicitly registered hash-only project mini-brains.",
    )
    lines.extend(
        _action_table(_action_rows(facts, {"evi-universe", "evi-bigger-universe"}))
    )
    lines.extend(
        dedent(
            """

            Neither surface merges per-project Project Truth, Memory, Learning, Canon, Plan, Goal, or HIL. A federation edge requires exact project identities, hash-bound summaries, scope, provenance, and an explicit link. Query results remain bounded and identify the owning project authority.

            Connector Brain stores connector/grant/federation control separately. Project Universe may reference connector integrity but cannot inherit connector permissions or external service authority.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _host_storage(facts: dict[str, Any]) -> str:
    lines = _header(
        "Host and storage matrix",
        "Host profile, lifetime, storage durability, model, reasoning effort, account tier, and transport are separate axes. None changes HIL law.",
    )
    lines.extend(
        dedent(
            """
            | Host profile | Durable project authority | Transport |
            | --- | --- | --- |
            | Codex Desktop, persistent local host | Project-scoped local SQLite | Native/local route; tunnel only for a proven gap |
            | Codex CLI, persistent local host | Project-scoped local SQLite | Native/local route or version-bound tunnel |
            | Persistent Codex VM | Mounted/local durable SQLite | Direct transport when available |
            | Ephemeral Codex VM with durable mount | Mounted SQLite | Exact VM-lifetime route |
            | Ephemeral Codex VM without durable mount | Explicit transactional connector | Fail closed without durable storage |

            The current host plane is Codex Desktop, Codex CLI, and Codex VM. ChatGPT and external model-agent planes are not mixed into this package. A caller-supplied PID, title, CWD, or host ID is not runtime attestation.

            The maintainer release registry exposes exactly two selectors: the verified Git-main stable slot and the versioned local-testing slot. Selector identity is a locator, not Project/PV, Plan, Goal, HIL, or runtime attestation.

            A storage connector owns persistence only for its explicit grant. It never becomes Project Truth, Plan, Goal, HIL, or MCP authority. Secrets remain in the host secret provider and are referenced by opaque handles only.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _install() -> str:
    lines = _header(
        "Codex 3.0 local installation and reload",
        "Installation is a maintainer release operation over exact reviewed package bytes. It is not a downstream project workflow and does not create or accept a Project Version.",
    )
    lines.extend(
        dedent(
            """
            ## Required order

            1. Verify the exact Git commit/tree and clean required CI results.
            2. Build the deterministic installable plugin package and executable manifest.
            3. Verify action/schema/SDK/MCP, skill, hook, authority, lane, tool, lock, license, and secret boundaries.
            4. Cache-bust and install into the explicitly selected existing local slot.
            5. Build the hidden hash-keyed runtime from the base/toolchain locks plus the exact selected CPU, NVIDIA CUDA, or AMD DirectML provider lock. Each profile has a distinct runtime key.
            6. Install/probe native dependencies and prewarm applicable grammar/model/tunnel capabilities.
            7. Run pre-restart package/catalog/runtime acceptance.
            8. After the response is complete, use the maintainer-local restart helper only when the host requires a same-task restart.
            9. Reopen the same Codex app, task, and workspace, then prove installed member/catalog/skill/hook/tool/authority parity.

            The universal default is CPU. A GPU profile requires the user's enabled vendor plugin/grant and compatible host proof; it never silently changes the package for every user. The helper is not installed as plugin business logic and owns no Plan, Goal, HIL, State Travel, package, or Git behavior. The tunnel is separate transport and does not install the plugin.

            The marketplace Upgrade button is used only after the exact main release/package route reaches its assigned row. A branch test install and main-slot install remain separately receipted until deliberate normalization.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _git_ci() -> str:
    lines = _header(
        "Git, CI, and merge boundary",
        "All repository delivery originates from the local worktree through the governed Git route. Git evidence never implies Project/PV acceptance or HIL.",
    )
    lines.extend(
        dedent(
            """
            1. Preserve the exact dirty/untracked working tree and build a reviewed staging allowlist.
            2. Run the required focused gates and Code-mode recursive Boolean correction loop until all applicable gates pass.
            3. Run one complete system-wide regression; after it, rerun only affected suites for bounded corrections.
            4. Run executable-plugin and full staged-tree fingerprint Refresh so changed and unchanged files receive current receipts.
            5. Commit and push the feature branch through the exact GitHub App/local Git route.
            6. Make every required GitHub check pass. Skipped checks are valid only when their workflow contract explicitly makes them inapplicable.
            7. Merge `main` only after required CI is green and the assigned release authority permits it.
            8. Install the exact main package into the main slot and refresh the local slot so local bytes cannot lag the merged release.

            No force-push, protected-branch rewrite, implicit merge, secret output, candidate acceptance, Fuse, production publication, or pointer movement is authorized by this workflow.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _release() -> str:
    lines = _header(
        "Release and compatibility",
        "The current source line is Evidence Lane 3.0.0. Source identity, local package identity, installed-host identity, Git commit/tree, runtime, candidate, accepted PV, website, and human release decision are separate facts.",
    )
    lines.extend(
        dedent(
            """
            Historical commits, packages, receipts, and accepted PVs retain their original identities as non-executable provenance. They cannot override the current registry or revive a purged compatibility route.

            A branch checkpoint, passing test, preview, or package is release evidence only. Main merge, main-slot installation, slot normalization, production website publication, Project HIL, Learning HIL, Fuse, and Goal completion remain separate operations.

            Downstream projects keep their own Git, CI, deployment, storage, schema, lane, and connector choices. They do not inherit the Evidence Lane plugin-maintainer release cycle.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _repository_map() -> str:
    lines = _header(
        "Repository map",
        "The repository separates the installable plugin, public documentation application, GitHub Markdown, Pages assets, tests, and repository-level generators.",
    )
    lines.extend(
        dedent(
            """
            | Path | Role |
            | --- | --- |
            | `plugins/evidence-lane-plugin/` | Installable Codex plugin |
            | `plugins/evidence-lane-plugin/src/evidence_lane_plugin/` | Canonical Python engine and internal SDK |
            | `plugins/evidence-lane-plugin/skills/` | Governed skills and routing manifests |
            | `plugins/evidence-lane-plugin/hooks/` | Host event classes and ordered handlers |
            | `plugins/evidence-lane-plugin/env/` | Clean ENV v17 action plane |
            | `plugins/evidence-lane-plugin/uop/` | Clean UOP v17 governance plane |
            | `plugins/evidence-lane-plugin/authorities/` | Named authorities and 18 sector-lane templates/workflows |
            | `plugins/evidence-lane-plugin/sdk/` | Internal/outer SDK bindings and workflows |
            | `plugins/evidence-lane-plugin/mcp/` | Package-local MCP binding and 91 action bindings |
            | `plugins/evidence-lane-plugin/toolchains/` | Conditional tool, accelerator-provider, license, routing, and architecture registries |
            | `apps/evidence-lane-app/` | Public documentation application |
            | `docs/` | Maintained GitHub Markdown pages |
            | `github-pages/` | GitHub Pages assets and projection |
            | `scripts/` | Repository-level docs/Pages/release generators |
            | `tests/` | Repository-wide executable verification |

            Local virtual environments, caches, runtimes, rehearsal evidence, RAG indexes, temporary support lanes, and secrets are excluded from release staging.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _tunnel() -> str:
    lines = _header(
        "User tunnel guide",
        "The Evidence Lane tunnel is a version-bound, project-neutral transport used only when the selected Codex host lacks the required direct MCP or host-tool route.",
    )
    lines.extend(
        dedent(
            """
            ## Boundary

            The tunnel does not install the plugin, register projects, expose 91 actions by itself, select workflows, own credentials, schedule work, decide HIL, move pointers, or become another agent. One host-wide tunnel can carry exact project/task identities for multiple independent tasks.

            ## Setup and prewarm

            Use `plugins/evidence-lane-plugin/scripts/windows_tunnel/Install-EvidenceLaneTunnel.ps1` only at the assigned maintainer row. The installer builds the exact hidden runtime, verifies all requirement and native-tool identities, prewarms applicable capabilities, stores masked secret material only through the host secret boundary, and creates the current versioned startup task when required.

            Use `Manage-EvidenceLaneTunnel.ps1 -Action Status` for readback. PASS requires the current tunnel ID, runtime key, binary/package hashes, host profile/lifetime, prewarm receipt, and hidden process state. An old scheduled task or runtime is directly removed when the new version becomes active.

            FastMCP remains the preferred composition route when eligible; the tunnel carries the selected route and never changes its authority.
            """
        )
        .strip()
        .splitlines()
    )
    lines.extend(_footer())
    return "\n".join(lines)


def _upstream_reference_provenance(
    facts: dict[str, Any],
    plugin_root: Path,
) -> str:
    """Refresh the current executable dependency ledger without rewriting pinned research."""

    repository_root = plugin_root.parents[1]
    path = repository_root / "docs" / "UPSTREAM_REFERENCE_PROVENANCE.md"
    base = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    first_line, remainder = base.split("\n", 1)
    if first_line.startswith("<!-- evidence-lane-public-docs-full-refresh:"):
        base = (
            "<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / registry-derived-v2 -->\n"
            + remainder
        )

    inventory = facts["licenses"]
    rows = list(inventory["rows"])
    if not (
        inventory["status"] == "PASS"
        and inventory["tool_requirement_count"] == len(rows)
        and inventory["license_classification_count"] == len(rows)
        and inventory["all_tool_requirements_classified"] is True
        and inventory["all_tool_requirements_have_physical_license_records"] is True
    ):
        raise RuntimeError("UPSTREAM_LICENSE_INVENTORY_PARITY_FAILED")

    classifications: dict[str, int] = {}
    for row in rows:
        key = str(row["classification"])
        classifications[key] = classifications.get(key, 0) + 1

    start = "<!-- EVIDENCE_LANE_CURRENT_UPSTREAM_INVENTORY_START -->"
    end = "<!-- EVIDENCE_LANE_CURRENT_UPSTREAM_INVENTORY_END -->"
    managed = [
        start,
        "",
        "## Current 3.0 executable dependency and license ledger",
        "",
        "The pinned repository research ledger below records inspected prior art. This separate current ledger is generated from the complete executable tool-requirement and physical license-record inventory. The two layers are intentionally not conflated: studying an upstream repository is different from conditionally using or redistributing a runtime capability.",
        "",
        "```mermaid",
        "flowchart TB",
        '    subgraph Research["Pinned research references"]',
        "      direction LR",
        '      Repo["Repository + commit + tree"] --> Study["Contract studied"] --> Boundary["Copied / adapted / excluded boundary"]',
        "    end",
        '    subgraph Runtime["Current executable dependencies"]',
        "      direction TB",
        '      Need["Condition-true tool requirement"] --> Route["Install or host-probe mode"] --> License["Physical license record"]',
        "    end",
        '    subgraph Proof["Release evidence"]',
        "      direction LR",
        '      License --> Hash["License-record SHA-256"] --> Receipt["Inventory receipt"]',
        '      Receipt -. mismatch .-> Stop["Fail closed before package or release"]',
        "    end",
        "    Boundary --> Need",
        "```",
        "",
        f"The current inventory contains **{len(rows)}** tool requirements and **{inventory['license_classification_count']}** license classifications. All requirements have physical license records. MCP remains a separate **91-action** inventory and is not counted as 119 tools.",
        "",
        "### Classification summary",
        "",
        "| Classification | Count |",
        "| --- | ---: |",
    ]
    managed.extend(
        f"| `{classification}` | {count} |"
        for classification, count in sorted(classifications.items())
    )
    managed.extend(
        [
            "",
            "### Complete current requirement ledger",
            "",
            "| # | Tool or capability | Requirement | Classification | Install mode | License or terms | Redistributed by source package | Physical license record |",
            "| ---: | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    def cell(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    for index, row in enumerate(rows, 1):
        managed.append(
            "| "
            + " | ".join(
                [
                    str(index),
                    f"`{cell(row['tool'])}`",
                    f"`{cell(row['requirement'])}`",
                    f"`{cell(row['classification'])}`",
                    f"`{cell(row['install_mode'])}`",
                    cell(row["license_expression_or_terms"]),
                    "yes" if row["redistributed_by_source_package"] else "no",
                    f"`{cell(row['license_record'])}` (`{cell(row['license_record_sha256'])}`)",
                ]
            )
            + " |"
        )
    managed.extend(
        [
            "",
            "### Current release boundary",
            "",
            "- A declared requirement is not execution proof. The selected route must still pass host availability, version, capability, credential, locality, license, and result checks.",
            "- Host-system tools are probed, not silently redistributed. Hidden-runtime distributions carry their own exact runtime license bundle. Package-internal components remain covered by the repository license and notices.",
            "- Optional indexes, services, connectors, observability systems, and deployment targets are condition-bound; they never replace SQLite Project Truth or become the acting agent.",
            "- A license or source-identity mismatch blocks package/release admission. It cannot be waived by tests, Git, CI, installation, or HIL discussion.",
            "",
            end,
        ]
    )
    block = "\n".join(managed)
    if start in base or end in base:
        if base.count(start) != 1 or base.count(end) != 1:
            raise RuntimeError("UPSTREAM_MANAGED_BLOCK_MALFORMED")
        before, tail = base.split(start, 1)
        _, after = tail.split(end, 1)
        base = before.rstrip() + "\n\n" + block + "\n\n" + after.lstrip()
    else:
        anchor = "## Documentation and service references"
        if anchor not in base:
            raise RuntimeError("UPSTREAM_REFERENCE_INSERTION_ANCHOR_MISSING")
        before, after = base.split(anchor, 1)
        base = before.rstrip() + "\n\n" + block + "\n\n" + anchor + after
    return base.rstrip() + "\n"


def render_technical_page(
    page: str,
    *,
    values: dict[str, dict[str, Any]],
    plugin_root: Path,
) -> str:
    facts = _facts(values)
    renderers = {
        "ARCHITECTURE.md": lambda: _architecture(facts),
        "docs/AI_LEARNING.md": lambda: _learning(facts),
        "docs/CANON_TASK_GRAPH_AND_INPUT_HIL.md": lambda: _canon(facts),
        "docs/CODEX_V300_LOCAL_INSTALL_AND_RELOAD.md": _install,
        "docs/GIT_AND_CI_CD.md": _git_ci,
        "docs/HOOKS.md": lambda: _hooks(facts),
        "docs/HOST_AND_STORAGE_MATRIX.md": lambda: _host_storage(facts),
        "docs/LIFECYCLE_AND_HIL.md": lambda: _lifecycle(facts),
        "docs/MCP.md": lambda: _mcp(facts),
        "docs/MEMORY.md": lambda: _memory(facts),
        "docs/PLAN_AND_CHANGE_DISPLAY.md": _plan,
        "docs/PROJECT_PV_CONTENT_ADDRESSED_STORAGE.md": _pv_storage,
        "docs/PROJECT_UNIVERSE.md": lambda: _universe(facts),
        "docs/RELEASE_AND_COMPATIBILITY.md": _release,
        "docs/REPOSITORY_MAP.md": _repository_map,
        "docs/SKILLS.md": lambda: _skills(facts),
        "docs/SOURCE_INTAKE_AND_LANES.md": lambda: _source_intake(facts),
        "docs/TOOLS.md": lambda: _tools(plugin_root),
        "docs/UPSTREAM_REFERENCE_PROVENANCE.md": lambda: _upstream_reference_provenance(
            facts, plugin_root
        ),
        "docs/USER_TUNNEL_GUIDE.md": _tunnel,
    }
    if page not in renderers:
        raise ValueError(f"UNSUPPORTED_TECHNICAL_PAGE:{page}")
    rendered = renderers[page]()
    if page == "docs/UPSTREAM_REFERENCE_PROVENANCE.md":
        return rendered
    return _enrich_technical_page(page, rendered, facts)


__all__ = ["TECHNICAL_PAGES", "render_technical_page"]

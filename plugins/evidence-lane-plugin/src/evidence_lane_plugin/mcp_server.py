"""Universal tool-only MCP contract for Codex and ChatGPT-capable hosts."""

from __future__ import annotations

import os
from typing import Any, Literal

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.types import Icon, ToolAnnotations
from pydantic import AnyHttpUrl
from starlette.requests import Request
from starlette.responses import JSONResponse

from .auth import OAuthJWTConfig, OAuthJWTVerifier, StaticBearerVerifier
from .constants import ENGINE_VERSION
from .github_automation_governance import apply_fastmcp_tool_filter
from .lane_engine import prewarm_native_dependencies
from .service import EvidenceLaneService

_PUBLIC_SITE_URL = "https://evidence-lane-chatgpt-mcp-adapter.vercel.app"

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_LOCAL_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_HIL_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=False,
)
_REMOTE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)


def _meta(label: str, done: str) -> dict[str, Any]:
    return {
        "openai/toolInvocation/invoking": label,
        "openai/toolInvocation/invoked": done,
    }


def create_mcp_server(
    *,
    service: EvidenceLaneService | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    bearer_token: str | None = None,
    base_url: str | None = None,
    oauth_config: OAuthJWTConfig | None = None,
    public_site_url: str | None = None,
    allowed_tool_names: str | tuple[str, ...] | list[str] | None = None,
) -> FastMCP:
    application = service or EvidenceLaneService()
    release_identity = application.engine.doctor()["engine"]
    auth = None
    verifier: TokenVerifier | None = None
    if bearer_token and oauth_config:
        raise ValueError("Choose either static bearer or OAuth JWT authentication.")
    if bearer_token:
        exact_base = (base_url or f"http://{host}:{port}").rstrip("/")
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(f"{exact_base}/"),
            resource_server_url=AnyHttpUrl(f"{exact_base}/"),
            required_scopes=["evidence-lane:read"],
        )
        verifier = StaticBearerVerifier(bearer_token)
    elif oauth_config:
        exact_base = (base_url or f"http://{host}:{port}").rstrip("/")
        auth = AuthSettings(
            issuer_url=AnyHttpUrl(oauth_config.issuer_url),
            resource_server_url=AnyHttpUrl(f"{exact_base}/"),
            required_scopes=list(oauth_config.required_scopes),
        )
        verifier = OAuthJWTVerifier(oauth_config)
    exact_public_site = (
        public_site_url
        or os.environ.get("EVIDENCE_LANE_PUBLIC_SITE_URL")
        or _PUBLIC_SITE_URL
    ).rstrip("/")
    mcp = FastMCP(
        "Evidence Lane Plugin",
        instructions=(
            "A prepared post-Fuse handoff makes /evi-state-travel eligible but "
            "never auto-selects or consumes it. Display and run State Travel only "
            "after an explicit user request or genuine host-context exhaustion. "
            "Otherwise start /evi with atomic /evi-boot plus locked ENV/UOP Flash "
            "as the first normal action, then display "
            "exactly Boot, Rollback, Build, Refresh, Mode, and Source Intake. "
            "Source Intake is one generalized ordered control for all eighteen "
            "lanes and Project Engulf and always includes Chat Lineage. Fuse "
            "requires exact APPROVE through pv_fuse and seals a fresh-window "
            "handoff without rebuilding. When explicitly triggered, State Travel "
            "verifies atomic Boot/Flash, the "
            "accepted pointer, and seals in a fresh Codex task or ChatGPT chat, "
            "then waits. A booted session remains active until /evi-exit-boot. "
            "Before every HIL or State Travel stop, visibly render the returned "
            "suggested_next_prompt. The host owns composer suggestions; never "
            "claim the MCP wrote the prompt bar and never auto-submit it. "
            "Never infer HIL approval, store private reasoning, expose connector "
            "secrets, or write remote "
            "Git without the exact governed action."
        ),
        website_url=exact_public_site,
        icons=[
            Icon(
                src=f"{exact_public_site}/evidence-lane-icon.png",
                mimeType="image/png",
                sizes=["256x256"],
            )
        ],
        host=host,
        port=port,
        streamable_http_path="/mcp",
        stateless_http=False,
        auth=auth,
        token_verifier=verifier,
    )
    # FastMCP 1.28.1 exposes website/icons but not its low-level server version.
    # Set the same pinned engine identity that clients read from pyproject.toml
    # instead of allowing the SDK's default 1.0.0 to leak into ChatGPT metadata.
    mcp._mcp_server.version = ENGINE_VERSION

    @mcp.custom_route(
        "/healthz",
        methods=["GET"],
        name="evidence-lane-health",
        include_in_schema=False,
    )
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "PASS",
                "service": "evidence-lane-plugin",
                "mcp_path": "/mcp",
                "release_sha": release_identity.get("commit"),
                "engine_version": release_identity.get("release"),
                "package_sha256": release_identity.get("package_sha256"),
            }
        )

    @mcp.tool(
        name="runtime_doctor",
        title="Check Evidence Lane runtime",
        description=(
            "Check Git, Python, SQLite FTS5, schema availability, durable local "
            "storage, engine identity, and optional Drive configuration. Performs "
            "no repository or pointer mutation."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Checking Evidence Lane runtime", "Runtime check complete"),
        structured_output=True,
    )
    def runtime_doctor() -> dict[str, Any]:
        return application.invoke("runtime_doctor", application.doctor)

    @mcp.tool(
        name="session_flash_status",
        title="Inspect Evidence Lane session flash",
        description=(
            "Verify the exact locked ENV15/UOP15 authority members, Mermaid hashes, "
            "read-only SQLite integrity, source-packet warning, and installation "
            "flash receipt without creating or changing the receipt."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Checking session flash", "Session flash status ready"),
        structured_output=True,
    )
    def session_flash_status() -> dict[str, Any]:
        return application.invoke(
            "session_flash_status", application.session_flash_status
        )

    @mcp.tool(
        name="runtime_activation_status",
        title="Inspect Evidence Lane runtime attachment",
        description=(
            "Read whether ENV/UOP Flash context and visible prompt/response capture "
            "are attached to governed sessions. DETACHED preserves the installed "
            "plugin, Flash verification receipt, immutable store, and pointer."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Checking runtime attachment", "Runtime attachment ready"),
        structured_output=True,
    )
    def runtime_activation_status() -> dict[str, Any]:
        return application.invoke(
            "runtime_activation_status", application.runtime_activation_status
        )

    @mcp.tool(
        name="lifecycle_transition_law",
        title="Read the canonical lifecycle law",
        description=(
            "Return the single executable event/from/to transition table used by "
            "session boot, PV build, task classification, Refresh, six-way HIL, "
            "rollback state travel, and fresh-window accepted-PV handoff."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading lifecycle law", "Lifecycle law ready"),
        structured_output=True,
    )
    def lifecycle_transition_law() -> dict[str, Any]:
        return application.invoke(
            "lifecycle_transition_law", application.transition_law
        )

    @mcp.tool(
        name="lane_catalog",
        title="List universal Evidence Lane sectors",
        description=(
            "Return the one immutable eighteen-lane registry, aliases, command "
            "mapping, parser/chunker contracts, SQLite names, FTS tables, and "
            "mutation policies. Performs no state mutation."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Loading lane registry", "Lane registry ready"),
        structured_output=True,
    )
    def lane_catalog() -> dict[str, Any]:
        return application.invoke("lane_catalog", application.lane_catalog)

    @mcp.tool(
        name="source_intake_classify",
        title="Classify generalized Source Intake",
        description=(
            "Auto-detect one or more ordered source pointers across all eighteen "
            "canonical lanes and Project Engulf, apply exact per-source overrides, "
            "always include Chat Lineage, and append a visible classification "
            "receipt. GOVERNED_CONTENT_REGISTRY additionally records deterministic "
            "read-only source identities without copying payloads, building a "
            "candidate, or moving a pointer."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Classifying Source Intake", "Source Intake classified"),
        structured_output=True,
    )
    def source_intake_classify(
        project_id: str,
        sources: list[str],
        overrides: dict[str, str] | None = None,
        session_id: str | None = None,
        git_mode: str = "AUTO",
        authority_mode: str = "CLASSIFICATION_ONLY",
        source_assertions: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_intake_classify",
            application.source_intake,
            project_id,
            sources,
            overrides=overrides,
            session_id=session_id,
            git_mode=git_mode,
            authority_mode=authority_mode,
            source_assertions=source_assertions,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_sqlite_inspect",
        title="Inspect registered SQLite brain authorities",
        description=(
            "Inspect every direct or ZIP-embedded SQLite authority in one governed "
            "Source Intake batch. Exact duplicate bytes are inspected once, ZIPs "
            "with proven extracted counterparts are skipped, independent embedded "
            "databases use bounded in-memory deserialization, and all SQLite reads "
            "remain query-only without executing imported SQL or moving a pointer."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Inspecting SQLite authorities", "SQLite authorities inspected"),
        structured_output=True,
    )
    def source_sqlite_inspect(
        project_id: str,
        batch_id: str,
        session_id: str | None = None,
        max_embedded_member_bytes: int = 768 * 1024 * 1024,
        exact_count_max_database_bytes: int = 32 * 1024 * 1024,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_sqlite_inspect",
            application.source_sqlite_inspect,
            project_id,
            batch_id,
            session_id=session_id,
            max_embedded_member_bytes=max_embedded_member_bytes,
            exact_count_max_database_bytes=exact_count_max_database_bytes,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_custom_schema_compile",
        title="Compile and map a Custom Source Schema",
        description=(
            "Validate one declarative, schema-first custom source contract and "
            "map it deterministically to a governed Source Intake batch. The "
            "compiler allows only pinned dependencies, ordered selectors, typed "
            "fields, and non-executable transforms; it reads sealed registry "
            "metadata only and never executes imported code or SQL, copies source "
            "payloads, builds a candidate, or moves a pointer."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Compiling Custom Source Schema", "Custom Source Schema mapped"),
        structured_output=True,
    )
    def source_custom_schema_compile(
        project_id: str,
        batch_id: str,
        schema_definition: dict[str, Any],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_custom_schema_compile",
            application.source_custom_schema_compile,
            project_id,
            batch_id,
            schema_definition,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_identity_register",
        title="Register distinct source-generation identities",
        description=(
            "Append a complete multi-axis identity matrix for one governed Source "
            "Intake batch. Artifact bytes, producer application/release, model, "
            "architecture generation, internal schema labels, observed filename "
            "markers, and claim authority remain separate. Alias/SAME_AS collapse "
            "is forbidden; unbound versions remain explicitly unclaimed."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Registering source identities", "Source identities registered"),
        structured_output=True,
    )
    def source_identity_register(
        project_id: str,
        batch_id: str,
        entities: list[dict[str, Any]],
        profiles: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_identity_register",
            application.source_identity_register,
            project_id,
            batch_id,
            entities=entities,
            profiles=profiles,
            relations=relations,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_graph_build",
        title="Build a bounded provenance-first source graph",
        description=(
            "Build a deterministic polyglot graph over exact registered Source "
            "Intake bytes. Stable semantic node IDs exclude mutable line numbers, "
            "every edge records EXTRACTED, INFERRED, or AMBIGUOUS provenance, "
            "coverage gaps remain visible, and ZIPs are skipped only with an exact "
            "Delta 067A extracted-counterpart receipt. Finite file, byte, node, and "
            "edge bounds apply; an optional repository-relative path-prefix selection "
            "is sealed and visibly reports omitted registered members. No source, "
            "candidate, or pointer is mutated."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Building bounded source graph", "Source graph built"),
        structured_output=True,
    )
    def source_graph_build(
        project_id: str,
        batch_id: str,
        occurrence_ordinals: list[int] | None = None,
        member_path_prefixes: list[str] | None = None,
        max_files: int = 25_000,
        max_total_bytes: int = 1024 * 1024 * 1024,
        max_file_bytes: int = 8 * 1024 * 1024,
        max_nodes: int = 500_000,
        max_edges: int = 1_000_000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_graph_build",
            application.source_graph_build,
            project_id,
            batch_id,
            occurrence_ordinals=occurrence_ordinals,
            member_path_prefixes=member_path_prefixes,
            max_files=max_files,
            max_total_bytes=max_total_bytes,
            max_file_bytes=max_file_bytes,
            max_nodes=max_nodes,
            max_edges=max_edges,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_graph_diff",
        title="Diff exact source-graph snapshots",
        description=(
            "Compare two exact registered graph roots by stable node and edge ID, "
            "separating added, removed, and content-changed entities. Samples are "
            "bounded and the full count projection is sealed without reading or "
            "mutating source bytes, candidates, or pointers."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Diffing source graphs", "Source graphs diffed"),
        structured_output=True,
    )
    def source_graph_diff(
        project_id: str,
        from_graph_id: str,
        to_graph_id: str,
        sample_limit: int = 100,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_graph_diff",
            application.source_graph_diff,
            project_id,
            from_graph_id,
            to_graph_id,
            sample_limit=sample_limit,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_graph_impact",
        title="Traverse a bounded affected source subgraph",
        description=(
            "From exact stable node IDs, traverse upstream dependents, downstream "
            "dependencies, or both across an explicit relation allowlist. Depth "
            "and node caps are mandatory, edge evidence retains source location "
            "and confidence, and the traversal never changes source or lifecycle "
            "state."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Traversing source impact", "Source impact traversed"),
        structured_output=True,
    )
    def source_graph_impact(
        project_id: str,
        graph_id: str,
        seed_node_ids: list[str],
        relations: list[str] | None = None,
        direction: str = "UPSTREAM",
        max_depth: int = 3,
        max_nodes: int = 1000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_graph_impact",
            application.source_graph_impact,
            project_id,
            graph_id,
            seed_node_ids,
            relations=relations,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_git_history_build",
        title="Seal full bounded Git history evidence",
        description=(
            "For one exact registered directory that still has local .git metadata, "
            "seal all reachable refs, commits, parent edges, objects, per-commit "
            "trees, per-parent file changes, renames, hunk coordinates, and changed-"
            "line hashes. Extracted folders never qualify as history; lazy fetch, "
            "source writes, Git writes, candidate creation, and pointer movement are "
            "forbidden. Finite bounds fail closed without a partial snapshot."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Sealing bounded Git history", "Git history sealed"),
        structured_output=True,
    )
    def source_git_history_build(
        project_id: str,
        batch_id: str,
        occurrence_ordinal: int,
        max_refs: int = 20_000,
        max_commits: int = 100_000,
        max_objects: int = 2_000_000,
        max_tree_entries: int = 5_000_000,
        max_file_changes: int = 2_000_000,
        max_hunks: int = 2_000_000,
        max_changed_lines: int = 5_000_000,
        max_patch_bytes: int = 2 * 1024 * 1024 * 1024,
        max_single_object_bytes: int = 1024 * 1024 * 1024,
        max_total_object_bytes: int = 8 * 1024 * 1024 * 1024,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_git_history_build",
            application.source_git_history_build,
            project_id,
            batch_id,
            occurrence_ordinal,
            max_refs=max_refs,
            max_commits=max_commits,
            max_objects=max_objects,
            max_tree_entries=max_tree_entries,
            max_file_changes=max_file_changes,
            max_hunks=max_hunks,
            max_changed_lines=max_changed_lines,
            max_patch_bytes=max_patch_bytes,
            max_single_object_bytes=max_single_object_bytes,
            max_total_object_bytes=max_total_object_bytes,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="source_git_commit_impact",
        title="Map an exact Git parent diff to graph impact",
        description=(
            "Bind one indexed commit and exact parent ordinal to FILE nodes from the "
            "same registered source occurrence, then traverse a bounded semantic "
            "impact graph. Removed, excluded, ambiguous, or absent paths stay "
            "explicitly unmapped; no historical semantic state is fabricated and no "
            "source, Git repository, candidate, or pointer is changed."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Mapping Git change impact", "Git change impact mapped"),
        structured_output=True,
    )
    def source_git_commit_impact(
        project_id: str,
        snapshot_id: str,
        graph_id: str,
        commit_sha: str,
        parent_ordinal: int = 0,
        relations: list[str] | None = None,
        direction: str = "UPSTREAM",
        max_depth: int = 3,
        max_nodes: int = 1000,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "source_git_commit_impact",
            application.source_git_commit_impact,
            project_id,
            snapshot_id,
            graph_id,
            commit_sha,
            parent_ordinal=parent_ordinal,
            relations=relations,
            direction=direction,
            max_depth=max_depth,
            max_nodes=max_nodes,
            session_id=session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="hil_intent_classify",
        title="Classify tolerant HIL intent",
        description=(
            "Classify visible natural-language continuation or approval intent, "
            "append it to Chat Lineage, and return the safe exact next action. "
            "This tool never decides HIL, promotes a candidate, or moves a pointer."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Classifying HIL intent", "HIL intent classified"),
        structured_output=True,
    )
    def hil_intent_classify(
        project_id: str,
        session_id: str,
        utterance: str,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "hil_intent_classify",
            application.classify_hil_intent,
            project_id,
            session_id,
            utterance,
            event_id=event_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="connector_plugin_catalog",
        title="Inspect governed connector and toolchain plugins",
        description=(
            "Read the append-only connector brain, its active maximum of eight, "
            "dropped history, SQLite integrity, capabilities, lanes, and secret-free "
            "configuration-variable names."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading connector brain", "Connector brain ready"),
        structured_output=True,
    )
    def connector_plugin_catalog(project_id: str) -> dict[str, Any]:
        return application.invoke(
            "connector_plugin_catalog",
            application.connector_plugin_catalog,
            project_id,
        )

    @mcp.tool(
        name="connector_plugin_settings",
        title="Open the eight-slot connector settings surface",
        description=(
            "Return eight host-specific connector slots for CODEX or CHATGPT, "
            "including governed role/schema and optional backend-runtime metadata. "
            "The profiles are independent and credential values remain host-managed."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading connector settings", "Connector settings ready"),
        structured_output=True,
    )
    def connector_plugin_settings(
        project_id: str,
        host_profile: Literal["CODEX", "CHATGPT"],
    ) -> dict[str, Any]:
        return application.invoke(
            "connector_plugin_settings",
            application.connector_plugin_settings,
            project_id,
            host_profile=host_profile,
        )

    @mcp.tool(
        name="connector_plugin_register",
        title="Register one bounded persistent connector or toolchain",
        description=(
            "Register one connector or AI toolchain plugin with environment-variable "
            "names only, explicit capabilities, and canonical lanes. At most eight "
            "additional plugins may remain active; no secret value is persisted."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Registering governed plugin", "Governed plugin registered"),
        structured_output=True,
    )
    def connector_plugin_register(
        project_id: str,
        plugin_id: str,
        name: str,
        plugin_kind: Literal["connector", "toolchain"],
        description: str,
        config_env_keys: list[str],
        capabilities: list[str],
        allowed_lanes: list[str],
        registered_by: str,
        purpose: str | None = None,
        allowed_actions: list[str] | None = None,
        write_scope: list[str] | None = None,
        expires_at: str = "NO_EXPIRY",
        role: str | None = None,
        role_schema: dict[str, str] | None = None,
        host_profiles: list[Literal["CODEX", "CHATGPT"]] | None = None,
        backend_runtime: Literal[
            "python", "java", "kotlin", "go", "rust", "cpp", "external_mcp"
        ] = "python",
    ) -> dict[str, Any]:
        return application.invoke(
            "connector_plugin_register",
            application.connector_plugin_register,
            project_id,
            plugin_id=plugin_id,
            name=name,
            plugin_kind=plugin_kind,
            description=description,
            config_env_keys=config_env_keys,
            capabilities=capabilities,
            allowed_lanes=allowed_lanes,
            registered_by=registered_by,
            purpose=purpose,
            allowed_actions=allowed_actions,
            write_scope=write_scope,
            expires_at=expires_at,
            role=role,
            role_schema=role_schema,
            host_profiles=host_profiles,
            backend_runtime=backend_runtime,
            lifecycle=True,
        )

    @mcp.tool(
        name="connector_plugin_drop",
        title="Drop one governed persistent plugin",
        description=(
            "Drop exactly one active connector/toolchain only with DROP:<plugin-id>; "
            "preserve its registration and event history rather than deleting it."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Dropping governed plugin", "Governed plugin dropped"),
        structured_output=True,
    )
    def connector_plugin_drop(
        project_id: str,
        plugin_id: str,
        confirmation: str,
        dropped_by: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "connector_plugin_drop",
            application.connector_plugin_drop,
            project_id,
            plugin_id=plugin_id,
            confirmation=confirmation,
            dropped_by=dropped_by,
            lifecycle=True,
        )

    @mcp.tool(
        name="connector_plugin_route",
        title="Route one capability through governed plugin policy",
        description=(
            "Evaluate active grant, capability/action, canonical lane, and host "
            "guards in a fixed order. Select the sole eligible plugin, or require "
            "one exact preferred plugin ID when multiple routes qualify; ambiguity "
            "and failed guards remain fail-closed."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Routing connector capability", "Connector route recorded"),
        structured_output=True,
    )
    def connector_plugin_route(
        project_id: str,
        capability: str,
        canonical_lane_id: str | None = None,
        host_profile: Literal["CODEX", "CHATGPT"] = "CODEX",
        preferred_plugin_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "connector_plugin_route",
            application.connector_plugin_route,
            project_id,
            capability=capability,
            canonical_lane_id=canonical_lane_id,
            host_profile=host_profile,
            preferred_plugin_id=preferred_plugin_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="storage_connector_inspect",
        title="Inspect the primary storage connector route",
        description=(
            "Read the project selection, effective host route, transactional durable "
            "capability, and Google Drive fallback boundary. This never stores a secret "
            "or changes the selected authority."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Inspecting storage route", "Storage route ready"),
        structured_output=True,
    )
    def storage_connector_inspect(
        project_id: str,
        host_kind: str | None = None,
        ephemeral: bool = False,
        server_has_durable_filesystem: bool | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "storage_connector_inspect",
            application.storage_connector_inspect,
            project_id,
            host=host_kind,
            ephemeral=ephemeral,
            server_has_durable_filesystem=server_has_durable_filesystem,
        )

    @mcp.tool(
        name="storage_connector_select",
        title="Select the project primary storage authority",
        description=(
            "Append an exact project storage selection for AUTO, durable local "
            "SQLite, or one configured transactional durable connector. The exact "
            "SELECT_STORAGE token is required; Drive remains an optional mirror."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Selecting storage authority", "Storage authority selected"),
        structured_output=True,
    )
    def storage_connector_select(
        project_id: str,
        mode: Literal["AUTO", "LOCAL_SQLITE", "CONFIGURED_DURABLE_CONNECTOR"],
        selected_by: str,
        reason: str,
        confirmation: str,
        connector_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "storage_connector_select",
            application.storage_connector_select,
            project_id,
            mode=mode,
            selected_by=selected_by,
            reason=reason,
            confirmation=confirmation,
            connector_id=connector_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="mode_classify",
        title="Classify an ENV15 mode intersection and lanes",
        description=(
            "At any lifecycle position, classify one or more locked ENV15 modes "
            "such as Analysis + Planning + Code, map them to canonical Evidence "
            "Lanes, always include Chat Lineage, append only the privacy-minimized "
            "classification receipt when a session is active, and return to the "
            "prior lifecycle position without creating a task, candidate, HIL, or "
            "pointer movement. The result includes visible ENV/UOP formulas, "
            "PCM/MBA operator receipts, controlled CI/CD requirements, and "
            "lane-specific meanings for the universal six HIL tokens; render those "
            "fields visibly and never substitute generic Code-mode HIL semantics."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Classifying operating modes and lanes",
            "Mode intersection classified",
        ),
        structured_output=True,
    )
    def mode_classify(
        project_id: str,
        request: str,
        explicit_modes: list[str] | None = None,
        session_id: str | None = None,
        custom_modes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "mode_classify",
            application.classify_mode,
            project_id,
            request,
            explicit_modes=explicit_modes,
            session_id=session_id,
            custom_modes=custom_modes,
            lifecycle=True,
        )

    @mcp.tool(
        name="lane_status",
        title="Inspect one lane authority",
        description=(
            "Inspect one accepted or explicitly named candidate lane: SQLite/MMD/DOT "
            "hashes, parser/tool capability states, pointer evidence, Refresh "
            "classification, and live-source freshness."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Checking lane authority", "Lane authority ready"),
        structured_output=True,
    )
    def lane_status(
        project_id: str,
        lane: str,
        pv_ref: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "lane_status",
            application.lane_status,
            project_id,
            lane,
            pv_ref=pv_ref,
        )

    @mcp.tool(
        name="lane_search",
        title="Search one lane with BM25 and TF-IDF",
        description=(
            "Search an accepted or explicitly named candidate lane using SQLite "
            "FTS5/BM25, explicit materialized TF-IDF, or deterministic "
            "reciprocal-rank hybrid retrieval. Results include source/chunk hashes, "
            "parser state, PV authority, and live freshness."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Searching lane evidence", "Lane search complete"),
        structured_output=True,
    )
    def lane_search(
        project_id: str,
        lane: str,
        query: str,
        pv_ref: str | None = None,
        limit: int = 20,
        retrieval: str = "hybrid",
    ) -> dict[str, Any]:
        return application.invoke(
            "lane_search",
            application.lane_search,
            project_id,
            lane,
            query,
            pv_ref=pv_ref,
            limit=limit,
            retrieval=retrieval,
        )

    @mcp.tool(
        name="lane_fetch",
        title="Fetch one exact lane source",
        description=(
            "Fetch one exact source registered in a lane with bounded text, hash, "
            "parser state, structured facts, PV authority, and freshness. Binary "
            "source bytes remain inside the immutable SQLite authority."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Fetching lane source", "Lane source ready"),
        structured_output=True,
    )
    def lane_fetch(
        project_id: str,
        lane: str,
        path: str,
        pv_ref: str | None = None,
        max_bytes: int = 100_000,
    ) -> dict[str, Any]:
        return application.invoke(
            "lane_fetch",
            application.lane_fetch,
            project_id,
            lane,
            path,
            pv_ref=pv_ref,
            max_bytes=max_bytes,
        )

    @mcp.tool(
        name="lane_configure_routes",
        title="Grant exact source-to-lane routes",
        description=(
            "Arm one named, one-candidate-only mapping from exact current source "
            "paths to canonical lanes. The next PV build consumes the grant, records "
            "it in lineage and routes.json, then relocks it. Accepted route authority "
            "is inherited by later Refreshes. This never edits source or promotes a PV."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Arming lane route grant", "Lane route grant armed"),
        structured_output=True,
    )
    def lane_configure_routes(
        project_id: str,
        session_id: str,
        overrides: dict[str, str],
        granted_by: str,
        grant_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "lane_configure_routes",
            application.configure_lane_routes,
            project_id,
            session_id,
            overrides=overrides,
            granted_by=granted_by,
            grant_id=grant_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_enroll_project",
        title="Enroll an external Git project",
        description=(
            "Adopt one exact local Git path or clone one credential-free HTTPS Git "
            "URL into the user-owned Evidence Lane store, verify owner/name/branch, "
            "and register it without overwriting lineage. Performs no remote Git "
            "write and builds no PV."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Enrolling governed project", "Project enrollment complete"),
        structured_output=True,
    )
    def pv_enroll_project(
        project_id: str,
        display_name: str,
        source: str,
        expected_owner: str,
        expected_name: str,
        branch: str,
        sensitivity: str = "PRIVATE",
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_enroll_project",
            application.enroll_project,
            project_id=project_id,
            display_name=display_name,
            source=source,
            expected_owner=expected_owner,
            expected_name=expected_name,
            branch=branch,
            sensitivity=sensitivity,
            lifecycle=True,
        )

    @mcp.tool(
        name="git_sync_selected",
        title="Fast-forward one selected Git branch",
        description=(
            "Fetch one explicit local Git source or credential-free HTTPS repository "
            "and one exact branch, verify identity and optional commit, preview "
            "changed paths against any active task, then apply only a clean "
            "fast-forward. In an active governed session, an explicit replacement "
            "flag may narrow authority to the exact already-checked-out branch and "
            "writes a receipt. It never pushes, merges divergent history, switches "
            "branches, or broadens branch authority."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Syncing selected Git branch", "Selected branch synchronized"),
        structured_output=True,
    )
    def git_sync_selected(
        project_id: str,
        source: str,
        branch: str,
        session_id: str | None = None,
        expected_commit: str | None = None,
        replace_registered_branch: bool = False,
    ) -> dict[str, Any]:
        return application.invoke(
            "git_sync_selected",
            application.sync_git_source,
            project_id=project_id,
            source=source,
            branch=branch,
            session_id=session_id,
            expected_commit=expected_commit,
            replace_registered_branch=replace_registered_branch,
            lifecycle=True,
        )

    @mcp.tool(
        name="project_register",
        title="Register one Git project",
        description=(
            "Register one explicitly authorized local Git repository and branch set "
            "in the private plugin store. Idempotent only when all authority fields "
            "match the existing registration."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Registering governed project", "Project registration complete"),
        structured_output=True,
    )
    def project_register(
        project_id: str,
        display_name: str,
        repository_path: str,
        expected_owner: str,
        expected_name: str,
        allowed_branches: list[str],
        sensitivity: str = "PRIVATE",
    ) -> dict[str, Any]:
        return application.invoke(
            "project_register",
            application.register_project,
            project_id=project_id,
            display_name=display_name,
            repository_path=repository_path,
            expected_owner=expected_owner,
            expected_name=expected_name,
            allowed_branches=allowed_branches,
            sensitivity=sensitivity,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_plan_tasks",
        title="Queue a linear multi-task plan",
        description=(
            "Append one bounded task plan to the project backlog. Every task keeps "
            "its own exact class, outcome, paths, tools, acceptance checks, and stop "
            "condition. Planning activates nothing: the one-agent/one-active-task "
            "law still requires task_classify for one queued task at a time."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Queuing linear task plan", "Linear task plan queued"),
        structured_output=True,
    )
    def pv_plan_tasks(
        project_id: str,
        tasks: list[dict[str, Any]],
        planned_by: str,
        plan_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_plan_tasks",
            application.plan_tasks,
            project_id,
            tasks=tasks,
            planned_by=planned_by,
            plan_id=plan_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_task_backlog",
        title="Read the linear task backlog",
        description=(
            "Read queued, active, accepted, follow-up, rolled-back, rejected, and "
            "failed task records. Performs no classification or state mutation."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading task backlog", "Task backlog ready"),
        structured_output=True,
    )
    def pv_task_backlog(project_id: str) -> dict[str, Any]:
        return application.invoke(
            "pv_task_backlog", application.task_backlog, project_id
        )

    @mcp.tool(
        name="pv_task_transition",
        title="Drop or supersede one Delta",
        description=(
            "Append one explicit DROP or SUPERSEDE transition to the immutable "
            "Delta lifecycle ledger. SUPERSEDE requires a different queued "
            "replacement task; neither operation deletes or reorders history."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Recording Delta transition", "Delta transition recorded"),
        structured_output=True,
    )
    def pv_task_transition(
        project_id: str,
        task_id: str,
        transition: Literal["DROP", "SUPERSEDE"],
        decided_by: str,
        reason: str,
        replacement_task_id: str | None = None,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_task_transition",
            application.transition_task,
            project_id,
            task_id=task_id,
            transition_name=transition,
            decided_by=decided_by,
            reason=reason,
            replacement_task_id=replacement_task_id,
            event_id=event_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="session_boot",
        title="Boot governed Evidence Lane session",
        description=(
            "Verify and idempotently flash the locked ENV15/UOP15 session authority, "
            "then boot one governed context for one user, workspace, project, host, "
            "agent, and source state. Boot attaches Flash context and visible "
            "prompt/response capture until /evi-exit-boot. Exit detaches the runtime "
            "but preserves the installed plugin, verified Flash receipt, immutable "
            "store, and pointer. Neither runtime context nor ENV/UOP bytes enter a "
            "PV. Remote or ephemeral hosts fail closed unless a transactional "
            "durable connector is configured."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Booting governed session", "Governed session ready"),
        structured_output=True,
    )
    def session_boot(
        project_id: str,
        user_id: str,
        workspace_id: str,
        host_kind: str,
        agent_id: str,
        ephemeral: bool = False,
        sandbox_id: str | None = None,
        runtime_context: dict[str, Any] | None = None,
        host_session_id: str | None = None,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "session_boot",
            application.boot_session,
            project_id=project_id,
            user_id=user_id,
            workspace_id=workspace_id,
            host=host_kind,
            agent_id=agent_id,
            sandbox_id=sandbox_id,
            ephemeral=ephemeral,
            runtime_context=runtime_context,
            host_session_id=host_session_id,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=server_has_durable_filesystem,
            lifecycle=True,
        )

    @mcp.tool(
        name="session_resume",
        title="Resume persistent Evidence Lane session",
        description=(
            "Bind a fresh Codex or ChatGPT host task to the one already-active "
            "governed session, preserving its accepted entry, pending candidate, "
            "exact HIL follow-up, pointer generation, and prompt-index boundary. "
            "Performs no PV build, promotion, rollback, or source mutation."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Resuming governed session", "Persistent session resumed"),
        structured_output=True,
    )
    def session_resume(
        project_id: str,
        host_kind: str,
        host_session_id: str,
        ephemeral: bool = False,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "session_resume",
            application.resume_session,
            project_id=project_id,
            host=host_kind,
            host_session_id=host_session_id,
            ephemeral=ephemeral,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=server_has_durable_filesystem,
            runtime_context=runtime_context,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_build_initial",
        title="Build initial PV1 candidate",
        description=(
            "Run the deterministic whole-source Git/code engine against a clean, "
            "authorized repository to create—but not approve—PV1 candidate. "
            "Mermaid rendering failure remains a warning and never invalidates a "
            "correct SQLite PV."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Building initial PV1 candidate", "PV1 candidate sealed"),
        structured_output=True,
    )
    def pv_build_initial(project_id: str, session_id: str) -> dict[str, Any]:
        return application.invoke(
            "pv_build_initial",
            application.build_initial,
            project_id,
            session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="task_classify",
        title="Classify one bounded code task",
        description=(
            "Create the one-agent/one-task contract: exact class, outcome, paths, "
            "tools, acceptance checks, write boundary, stop condition, and HIL gate. "
            "This tool does not execute or broaden the task."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Classifying bounded task", "Task contract ready"),
        structured_output=True,
    )
    def task_classify(
        project_id: str,
        session_id: str,
        task_class: str,
        requested_outcome: str,
        permitted_paths: list[str],
        permitted_tools: list[str],
        acceptance_checks: list[str],
        stop_condition: str,
        backlog_task_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "task_classify",
            application.sessions.classify,
            project_id,
            session_id,
            task_class=task_class,
            requested_outcome=requested_outcome,
            permitted_paths=permitted_paths,
            permitted_tools=permitted_tools,
            acceptance_checks=acceptance_checks,
            stop_condition=stop_condition,
            backlog_task_id=backlog_task_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="task_record_activity",
        title="Append visible task evidence",
        description=(
            "Append one visible, operational, reproducible prompt/tool/command/file/"
            "test/build/diff/output/warning/error/usage event to redacted, idempotent "
            "ChatLineage. Private model reasoning and secrets are excluded."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Appending visible task evidence", "Task evidence appended"),
        structured_output=True,
    )
    def task_record_activity(
        project_id: str,
        session_id: str,
        activity_type: str,
        visible_payload: dict[str, Any],
        event_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "task_record_activity",
            application.sessions.record_activity,
            project_id,
            session_id,
            activity_type=activity_type,
            visible_payload=visible_payload,
            event_id=event_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="task_confirm_source_update",
        title="Confirm final host source state",
        description=(
            "Confirm the exact host-specific source boundary before Refresh. ChatGPT "
            "requires USER_APPLIED_AND_PULL_CONFIRMED. Codex hosts require "
            "HOST_SANDBOX_FINAL_STATE_CONFIRMED. This tool does not pull or mutate "
            "source itself."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Confirming final source state", "Final source state confirmed"),
        structured_output=True,
    )
    def task_confirm_source_update(
        project_id: str,
        session_id: str,
        confirmation: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "task_confirm_source_update",
            application.sessions.confirm_source_update,
            project_id,
            session_id,
            confirmation=confirmation,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_refresh",
        title="Build PV Refresh candidate",
        description=(
            "Rerun the same deterministic engine against the complete confirmed "
            "final repository state, calculate exact file Delta, append lineage, "
            "and seal the next candidate. Entry and exit slips are automatic internal "
            "artifacts. It never promotes or pushes remotely."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Building PV Refresh candidate", "PV Refresh candidate sealed"),
        structured_output=True,
    )
    def pv_refresh(project_id: str, session_id: str) -> dict[str, Any]:
        return application.invoke(
            "pv_refresh",
            application.refresh,
            project_id,
            session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="task_complete_and_refresh",
        title="Complete task and automatically seal exit PV",
        description=(
            "Confirm the exact host-specific final source boundary and immediately "
            "run deterministic Refresh in one governed operation. Entry and exit "
            "slips are generated automatically, the candidate remains unaccepted, "
            "and the result stops at the six-way HIL. An optional exact ordered "
            "batch can append QUEUED -> ACTIVE -> DONE for every queued Delta only "
            "when each task has bounded implementation and verification evidence. "
            "Users do not need a separate Refresh or exit command."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Completing task and sealing exit candidate",
            "Exit candidate sealed; HIL required",
        ),
        structured_output=True,
    )
    def task_complete_and_refresh(
        project_id: str,
        session_id: str,
        confirmation: str,
        batch_task_evidence: list[dict[str, Any]] | None = None,
        batch_completion_confirmation: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "task_complete_and_refresh",
            application.complete_task_and_refresh,
            project_id,
            session_id,
            confirmation=confirmation,
            batch_task_evidence=batch_task_evidence,
            batch_completion_confirmation=batch_completion_confirmation,
            lifecycle=True,
        )

    @mcp.tool(
        name="hil_decide",
        title="Record exact six-way HIL decision",
        description=(
            "Record APPROVE_WITH_DELTA, MORE_RESEARCH, REJECT, FAIL, or pointer-only "
            "ROLLBACK for one pending candidate. Exact APPROVE is deliberately "
            "rejected here and may promote only through pv_fuse. "
            "ROLLBACK preserves the candidate and accepted history; a bare target "
            "resolves to the current prompt/session entry PV."
        ),
        annotations=_HIL_WRITE,
        meta=_meta("Recording human HIL decision", "HIL decision recorded"),
        structured_output=True,
    )
    def hil_decide(
        project_id: str,
        session_id: str,
        decision: str,
        decided_by: str,
        reason: str | None = None,
        correction_delta: str | None = None,
        research_question: str | None = None,
        rollback_to: str | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "hil_decide",
            application.record_hil_decision,
            project_id,
            session_id,
            decision=decision,
            decided_by=decided_by,
            reason=reason,
            correction_delta=correction_delta,
            research_question=research_question,
            rollback_to=rollback_to,
            decision_id=decision_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_fuse",
        title="Fuse candidate with exact APPROVE",
        description=(
            "Require the exact case-sensitive token APPROVE, promote the pending "
            "candidate byte-for-byte with compare-and-swap, and seal the exact "
            "accepted pointer for State Travel into a fresh Codex task or ChatGPT "
            "chat. No rebuild or remake occurs."
        ),
        annotations=_HIL_WRITE,
        meta=_meta(
            "Fusing approved PV candidate",
            "PV fused; fresh-window State Travel required",
        ),
        structured_output=True,
    )
    def pv_fuse(
        project_id: str,
        session_id: str,
        approval: str,
        decided_by: str,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_fuse",
            application.fuse,
            project_id,
            session_id,
            approval=approval,
            decided_by=decided_by,
            decision_id=decision_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_state_travel_prepare",
        title="Prepare accepted PV State Travel",
        description=(
            "Idempotently seal the accepted PV, pointer generation, manifest, and "
            "package hashes for a host-mediated fresh Codex task or ChatGPT chat. "
            "This does not claim that the host window was opened."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Preparing accepted PV State Travel",
            "State Travel handoff prepared",
        ),
        structured_output=True,
    )
    def pv_state_travel_prepare(
        project_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_state_travel_prepare",
            application.prepare_state_travel,
            project_id,
            session_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_state_travel_resume",
        title="Verify State Travel in a fresh host window",
        description=(
            "In the fresh Codex task or ChatGPT chat, verify the locked Flash, bind "
            "the new host session, enter the exact accepted PV without rebuilding, "
            "verify pointer generation and package seals, then stop in "
            "WAITING_FOR_NEXT_USER_COMMAND."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Verifying State Travel entry",
            "State Travel verified; waiting for user",
        ),
        structured_output=True,
    )
    def pv_state_travel_resume(
        project_id: str,
        session_id: str,
        handoff_id: str,
        host: str,
        host_session_id: str,
        ephemeral: bool,
        client_can_edit_source: bool | None = None,
        server_has_durable_filesystem: bool | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_state_travel_resume",
            application.resume_state_travel,
            project_id=project_id,
            session_id=session_id,
            handoff_id=handoff_id,
            host=host,
            host_session_id=host_session_id,
            ephemeral=ephemeral,
            client_can_edit_source=client_can_edit_source,
            server_has_durable_filesystem=server_has_durable_filesystem,
            runtime_context=runtime_context,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_rollback",
        title="Travel to an immutable accepted PV",
        description=(
            "Move only the accepted pointer to any immutable accepted PV after a "
            "compare-and-swap check. Target PVn directly, use PROMPT <index> or TURN "
            "<id>, or omit the target to use the current prompt/session entry PV. "
            "Accepted history, candidates, source bytes, lane databases, and the "
            "monotonic next-PV ordinal are preserved. This is an explicit HIL action "
            "and never restores or rewrites the live source."
        ),
        annotations=_HIL_WRITE,
        meta=_meta("Verifying rollback state travel", "Rollback state travel recorded"),
        structured_output=True,
    )
    def pv_rollback(
        project_id: str,
        session_id: str,
        decided_by: str,
        rollback_to: str | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_rollback",
            application.rollback,
            project_id,
            session_id,
            decided_by=decided_by,
            rollback_to=rollback_to,
            decision_id=decision_id,
            lifecycle=True,
        )

    @mcp.tool(
        name="hil_return_to_accepted",
        title="Return a rejected or failed run to accepted state",
        description=(
            "After REJECT or FAIL, verify that an accepted PV exists, the accepted "
            "pointer did not move, and the live repository was restored to that exact "
            "accepted source. Then clear only the bounded run state and append a "
            "visible return receipt. This never moves the accepted pointer."
        ),
        annotations=_HIL_WRITE,
        meta=_meta(
            "Verifying return to accepted state",
            "Returned to accepted state without pointer movement",
        ),
        structured_output=True,
    )
    def hil_return_to_accepted(
        project_id: str,
        session_id: str,
        reason: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "hil_return_to_accepted",
            application.sessions.return_to_accepted,
            project_id,
            session_id,
            reason=reason,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_begin_next_turn",
        title="Enter next turn from latest accepted PV",
        description=(
            "Enter the next accepted-PV turn. A prepared State Travel handoff "
            "remains blocking by default. When the user explicitly chooses to "
            "continue in the unchanged host, pass continue_same_host=true and the "
            "exact reason EXPLICIT_USER_CONTINUATION; the sealed receipt is "
            "preserved and visibly superseded without pointer movement."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Entering next accepted PV", "Next PV entry ready"),
        structured_output=True,
    )
    def pv_begin_next_turn(
        project_id: str,
        session_id: str,
        continue_same_host: bool = False,
        continuation_reason: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_begin_next_turn",
            application.sessions.begin_next_turn,
            project_id,
            session_id,
            continue_same_host=continue_same_host,
            continuation_reason=continuation_reason,
            lifecycle=True,
        )

    @mcp.tool(
        name="session_close",
        title="Close governed session",
        description=(
            "Close one governed session with a visible reason and release the "
            "one-session gate. Detach Flash context and prompt/response capture while "
            "preserving the installed plugin, locked Flash verification receipt, "
            "PVs, candidates, lineage, backlog, immutable store, and pointer."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta("Closing governed session", "Governed session closed"),
        structured_output=True,
    )
    def session_close(
        project_id: str,
        session_id: str,
        reason: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "session_close",
            application.sessions.close,
            project_id,
            session_id,
            reason=reason,
            lifecycle=True,
        )

    @mcp.tool(
        name="pv_status",
        title="Read project and pointer status",
        description=(
            "Read registered project authority, accepted pointer generation, "
            "immutable accepted PVs, and preserved candidates. Performs no write."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading PV status", "PV status ready"),
        structured_output=True,
    )
    def pv_status(project_id: str) -> dict[str, Any]:
        return application.invoke(
            "pv_status",
            application.status,
            project_id,
        )

    @mcp.tool(
        name="prompt_index_status",
        title="Read prompt-entry rollback index",
        description=(
            "Read bounded prompt indexes, turn IDs, entry PVs, pointer generations, "
            "and record hashes for the currently bound host task. Raw prompt text and "
            "private model reasoning are never stored."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading prompt-entry index", "Prompt-entry index ready"),
        structured_output=True,
    )
    def prompt_index_status(
        project_id: str,
        session_id: str,
        limit: int = 20,
    ) -> dict[str, Any]:
        return application.invoke(
            "prompt_index_status",
            application.prompt_index_status,
            project_id,
            session_id,
            limit=limit,
        )

    @mcp.tool(
        name="search",
        title="Search accepted PV source intelligence",
        description=(
            "Progressive read-only search across deterministic FTS chunks, symbols, "
            "and paths. Defaults to the current accepted PV; an explicitly named "
            "candidate is labeled UNACCEPTED_CANDIDATE and never presented as truth."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Searching PV source intelligence", "PV search complete"),
        structured_output=True,
    )
    def search(
        project_id: str,
        query: str,
        pv_ref: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return application.invoke(
            "search",
            application.reader.search,
            project_id,
            query,
            pv_ref=pv_ref,
            limit=limit,
        )

    @mcp.tool(
        name="fetch",
        title="Fetch exact PV file or chunk",
        description=(
            "Standard read-only fetch for file:<path>, chunk:<id>, or symbol:<id>. "
            "Text files support bounded line windows with exact file hash and source "
            "commit provenance; binary output is bounded base64."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Fetching exact PV evidence", "PV evidence fetched"),
        structured_output=True,
    )
    def fetch(
        project_id: str,
        ref_id: str,
        pv_ref: str | None = None,
        max_bytes: int = 256000,
        start_line: int | None = None,
        end_line: int | None = None,
        max_lines: int = 400,
    ) -> dict[str, Any]:
        return application.invoke(
            "fetch",
            application.reader.fetch,
            project_id,
            ref_id,
            pv_ref=pv_ref,
            max_bytes=max_bytes,
            start_line=start_line,
            end_line=end_line,
            max_lines=max_lines,
        )

    @mcp.tool(
        name="pv_summary",
        title="Read deterministic PV summary",
        description=(
            "Read repository identity, file/chunk/symbol/import/dependency/route "
            "counts, and code-family distribution from one validated PV."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Reading PV summary", "PV summary ready"),
        structured_output=True,
    )
    def pv_summary(
        project_id: str,
        pv_ref: str | None = None,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_summary",
            application.reader.project_summary,
            project_id,
            pv_ref=pv_ref,
        )

    @mcp.tool(
        name="pv_query",
        title="Run focused PV intelligence query",
        description=(
            "Run one allowlisted read-only query kind: files, symbols, imports, "
            "dependencies, routes, or receipts. Arbitrary SQL and multiple "
            "statements are blocked."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Querying PV intelligence", "PV query complete"),
        structured_output=True,
    )
    def pv_query(
        project_id: str,
        query_kind: str,
        pv_ref: str | None = None,
        value: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_query",
            application.reader.query,
            project_id,
            query_kind,
            pv_ref=pv_ref,
            value=value,
            limit=limit,
        )

    @mcp.tool(
        name="pv_diff",
        title="Compare two immutable PVs",
        description=(
            "Read exact added, modified, and deleted file identities between two "
            "validated accepted or candidate PVs. Performs no source or pointer write."
        ),
        annotations=_READ_ONLY,
        meta=_meta("Comparing PVs", "PV comparison complete"),
        structured_output=True,
    )
    def pv_diff(
        project_id: str,
        left_pv: str,
        right_pv: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "pv_diff",
            application.reader.diff,
            project_id,
            left_pv,
            right_pv,
        )

    @mcp.tool(
        name="remote_git_prepare_push",
        title="Prepare separately gated Git push",
        description=(
            "Prepare—but do not execute—one remote branch push bound to the current "
            "accepted PV and pointer generation. Returns a one-use exact confirmation "
            "token that must be provided through a separate explicit user action."
        ),
        annotations=_LOCAL_WRITE,
        meta=_meta(
            "Preparing remote Git action", "Remote Git action awaiting confirmation"
        ),
        structured_output=True,
    )
    def remote_git_prepare_push(
        project_id: str,
        requested_by: str,
        remote: str,
        local_ref: str,
        remote_branch: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "remote_git_prepare_push",
            application.remote_git.prepare_push,
            project_id,
            requested_by=requested_by,
            remote=remote,
            local_ref=local_ref,
            remote_branch=remote_branch,
            lifecycle=True,
        )

    @mcp.tool(
        name="remote_git_execute_push",
        title="Execute confirmed Git branch push",
        description=(
            "Execute exactly one previously prepared remote branch push only when "
            "the accepted pointer is unchanged and the exact one-use confirmation "
            "token is supplied. Never merges or approves a pull request."
        ),
        annotations=_REMOTE_WRITE,
        meta=_meta("Executing confirmed remote Git push", "Remote Git push finished"),
        structured_output=True,
    )
    def remote_git_execute_push(
        project_id: str,
        action_id: str,
        confirmation_token: str,
        confirmed_by: str,
    ) -> dict[str, Any]:
        return application.invoke(
            "remote_git_execute_push",
            application.remote_git.execute_push,
            project_id,
            action_id=action_id,
            confirmation_token=confirmation_token,
            confirmed_by=confirmed_by,
            lifecycle=True,
        )

    exposure_receipt = apply_fastmcp_tool_filter(mcp, allowed_tool_names)
    mcp._evidence_lane_tool_exposure_receipt = exposure_receipt  # type: ignore[attr-defined]
    return mcp


def run_server(
    *,
    transport: Literal["stdio", "sse", "streamable-http"] = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    bearer = os.environ.get("EVIDENCE_LANE_MCP_BEARER_TOKEN", "").strip()
    base_url = os.environ.get("EVIDENCE_LANE_MCP_BASE_URL", "").strip() or None
    oauth_values = {
        "issuer_url": os.environ.get("EVIDENCE_LANE_MCP_OAUTH_ISSUER_URL", "").strip(),
        "jwks_url": os.environ.get("EVIDENCE_LANE_MCP_OAUTH_JWKS_URL", "").strip(),
        "audience": os.environ.get("EVIDENCE_LANE_MCP_OAUTH_AUDIENCE", "").strip(),
    }
    oauth_any = any(oauth_values.values())
    oauth_complete = all(oauth_values.values())
    if oauth_any and not oauth_complete:
        missing = sorted(key for key, value in oauth_values.items() if not value)
        raise RuntimeError(
            "Incomplete OAuth configuration; missing: " + ", ".join(missing)
        )
    if bearer and oauth_complete:
        raise RuntimeError("Configure either static bearer or OAuth, not both.")
    oauth_config = None
    if oauth_complete:
        scopes = tuple(
            item
            for item in os.environ.get(
                "EVIDENCE_LANE_MCP_OAUTH_SCOPES",
                "evidence-lane:read evidence-lane:write",
            ).split()
            if item
        )
        algorithms = tuple(
            item.strip()
            for item in os.environ.get(
                "EVIDENCE_LANE_MCP_OAUTH_ALGORITHMS", "RS256"
            ).split(",")
            if item.strip()
        )
        oauth_config = OAuthJWTConfig(
            issuer_url=oauth_values["issuer_url"],
            jwks_url=oauth_values["jwks_url"],
            audience=oauth_values["audience"],
            required_scopes=scopes,
            algorithms=algorithms,
        )
    if transport == "streamable-http" and host not in {"127.0.0.1", "localhost", "::1"}:
        if not bearer and oauth_config is None:
            raise RuntimeError(
                "Non-loopback HTTP requires static bearer or OAuth authentication."
            )
        if not base_url or not base_url.startswith("https://"):
            raise RuntimeError(
                "Non-loopback HTTP requires an HTTPS EVIDENCE_LANE_MCP_BASE_URL."
            )
    application = EvidenceLaneService()
    application.sessions.ensure_installation()
    # Native OCR/ONNX dependencies must be loaded before FastMCP starts its
    # event loop.  Lane execution remains parallel; the cached engine is only
    # serialized at its documented shared call boundary.
    prewarm_native_dependencies()
    server = create_mcp_server(
        service=application,
        host=host,
        port=port,
        bearer_token=bearer or None,
        base_url=base_url,
        oauth_config=oauth_config,
        allowed_tool_names=os.environ.get("EVIDENCE_LANE_MCP_ALLOWED_TOOLS"),
    )
    server.run(transport=transport)

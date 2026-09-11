"""Lane-specific derived view contracts over separate owning databases.

A contract owns its folder, selected formats, graph meaning and optional
snapshot-navigation pointer. There is no universal four-file requirement.
"""
from __future__ import annotations

import hashlib
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from pydantic import Field

from .errors import LaneError
from .migrations import Migration, read_compatibility
from .registry import Contract
from .storage import json_text


class ViewScope(Contract):
    node_limit: int = Field(default=100, ge=1, le=200)
    edge_limit: int = Field(default=200, ge=0, le=500)
    query: str | None = Field(default=None, min_length=1, max_length=500)
    include_history: bool = False


def content_digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


class ViewGraph:
    """Bounded semantic records. Dropped endpoints/edges are always disclosed."""
    def __init__(self, project_id, scope):
        self.project_id, self.scope = project_id, scope
        self.nodes, self.edges = {}, []
        self.truncated = False
        self._edges = set()

    def node(self, kind, key, label, *, state=None, locator=None):
        identity = "n" + content_digest([self.project_id, kind, key])
        if identity in self.nodes:
            return identity
        if len(self.nodes) >= self.scope.node_limit:
            self.truncated = True
            return None
        self.nodes[identity] = {"id": identity, "kind": kind, "key": key, "label": str(label)[:240],
                                "state": state, "locator": locator or {}}
        return identity

    def edge(self, source, target, kind, *, provenance=None, evidence=None):
        if source is None or target is None:
            self.truncated = True
            return
        identity = (source, target, kind)
        if identity in self._edges:
            return
        if source not in self.nodes or target not in self.nodes:
            self.truncated = True
            return
        if len(self.edges) >= self.scope.edge_limit:
            self.truncated = True
            return
        self._edges.add(identity)
        self.edges.append({"source": source, "target": target, "kind": kind,
                           "provenance": provenance, "evidence": evidence})

    def result(self):
        return {"nodes": list(self.nodes.values()), "edges": self.edges, "truncated": self.truncated}


@dataclass(frozen=True)
class LaneView:
    view_id: str
    lane_id: str
    folder: str
    meaning: str
    producer: Callable
    head_owners: tuple[str, ...]
    migrations: tuple[Migration, ...]
    mmd_filename: str | None = None
    dot_filename: str | None = None
    pointer_filename: str | None = None
    pointer: Callable | None = None
    supports_query: bool = False
    supports_history: bool = False
    version: int = 1
    consumer: str = "artifact_export"
    node_kinds: tuple[str, ...] = ()
    edge_kinds: tuple[str, ...] = ()
    head_reader: Callable | None = None
    implementation_digest: str = field(init=False)

    def __post_init__(self):
        modules = {Path(inspect.getsourcefile(self.producer)), Path(__file__),
                   Path(__file__).with_name('artifact_contract.py'), Path(__file__).with_name('graph_pipeline.py')}
        if self.head_reader:
            modules.add(Path(inspect.getsourcefile(self.head_reader)))
        identity = [(path.name, hashlib.sha256(path.read_bytes()).hexdigest()) for path in sorted(modules)]
        object.__setattr__(self, 'implementation_digest', content_digest(identity))

    def validate(self):
        identifier = r"[a-z][a-z0-9_]{0,47}"
        if (not re.fullmatch(identifier + r"\." + identifier, self.view_id)
                or not re.fullmatch(identifier, self.lane_id) or not self.view_id.startswith(self.lane_id + ".")
                or not re.fullmatch(r"(authorities|sectors)/" + identifier, self.folder)
                or PurePosixPath(self.folder).name != self.lane_id or self.version < 1):
            raise LaneError("INVALID_VIEW_CONTRACT", "A view requires a canonical lane, version and owned folder.")
        names = [value for value in (self.mmd_filename, self.dot_filename, self.pointer_filename) if value]
        if not names or len(set(names)) != len(names) or any(not re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", value) for value in names):
            raise LaneError("INVALID_VIEW_CONTRACT", "Declare a distinct safe filename for each selected artifact role.")
        if bool(self.pointer_filename) != bool(self.pointer):
            raise LaneError("INVALID_VIEW_CONTRACT", "A pointer requires its own lane-specific navigation meaning.")
        if not self.meaning.strip() or not self.consumer.strip():
            raise LaneError("INVALID_VIEW_CONTRACT", "Every view requires explicit semantics and a consuming workflow.")
        if (not self.node_kinds or any(not re.fullmatch(r'[a-z][a-z0-9_]*', key) for key in self.node_kinds)
                or any(not re.fullmatch(r'[A-Z][A-Z0-9_]*', key) for key in self.edge_kinds)):
            raise LaneError("INVALID_VIEW_CONTRACT", "A lane view must declare its own node and relationship meanings.")

    def schema(self):
        return {"view_id": self.view_id, "lane_id": self.lane_id, "folder": self.folder,
            "version": self.version, "meaning": self.meaning, "consumer": self.consumer,
            "implementation_digest": self.implementation_digest,
            "files": {key: value for key, value in (("mmd", self.mmd_filename), ("dot", self.dot_filename),
                       ("pointer", self.pointer_filename)) if value},
            "scope": {"query": self.supports_query, "include_history": self.supports_history, "max_nodes": 200, "max_edges": 500},
            "source_owners": list(self.head_owners),
            "node_kinds": list(self.node_kinds), "edge_kinds": list(self.edge_kinds),
            "schemas": [{"owner": item.owner, "version": item.version, "digest": item.digest} for item in self.migrations],
            "authority": "owning_lane_sqlite", "current_selector": "owning_lane_sqlite",
            "head_reader": self.head_reader.__name__ if self.head_reader else 'declared_owner_events',
            "pointer_scope": "immutable_snapshot_navigation" if self.pointer else None,
            "shared_tools": {"mmd": {"primary": "langgraph_graph_exporter", "dependencies": ["langgraph", "langchain-core", "graphviz", "rustworkx"]},
                "dot": {"primary": "python_graphviz", "dependencies": ["langgraph", "langchain-core", "graphviz", "rustworkx"],
                    "native_validation": "explicit_native_choice_requires_shared_binary_and_supported_client_profile"}},
            "graph_execution": {"analysis": "bounded_rustworkx", "default_dot_validation": "source",
                "native_failure_fallback": False, "package_projection_runtime_observations": False}}

    @property
    def digest(self):
        return content_digest(self.schema())

    def source_head(self, store):
        from .lanes import is_named_custom_lane, lane_for_schema_owner
        from .storage import LaneStore
        project = store.project if isinstance(store, LaneStore) else store
        target = project.lane(self.lane_id) if is_named_custom_lane(self.lane_id) else project
        compatibility = read_compatibility(target, self.migrations)
        with project.connection(read_only=True):
            if self.head_reader:
                return {'owners': self.head_reader(project), 'schemas': compatibility}
            heads = {}
            for owner in self.head_owners:
                table, column = owner + "_events", "cursor" if owner == "lineage" else "digest"
                if not re.fullmatch(r"[a-z][a-z0-9_]*", owner):
                    raise LaneError("INVALID_VIEW_CONTRACT", "Source owners must be canonical schema names.")
                lane = project.lane(lane_for_schema_owner(owner).canonical_lane_id)
                with lane.connection(read_only=True) as connection:
                    exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE name=?", (table,)).fetchone()
                    row = connection.execute(f"SELECT sequence,{column} FROM {table} ORDER BY sequence DESC LIMIT 1").fetchone() if exists else None
                    heads[owner] = {"sequence": row[0], "digest": row[1]} if row else None
            if "plan" in self.head_owners:
                with project.lane('plan').connection(read_only=True) as connection:
                    exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='plan_current'").fetchone()
                    row = connection.execute("SELECT revision FROM plan_current WHERE singleton=1").fetchone() if exists else None
                    heads["plan_revision"] = row[0] if row else None
        return {"owners": heads, "schemas": compatibility}

    def project(self, store, scope):
        if scope.query is not None and not self.supports_query:
            raise LaneError("VIEW_SCOPE_UNSUPPORTED", "This lane view does not support text search.")
        if scope.include_history and not self.supports_history:
            raise LaneError("VIEW_SCOPE_UNSUPPORTED", "This lane view only describes its current authoritative state.")
        from .storage import LaneStore
        return self.producer(store.project if isinstance(store, LaneStore) else store, scope)


def plan_pointer(binding, graph, files):
    return {"schema": "evidence-lane.plan-navigation.v4", "project_id": binding["project_id"],
        "plan_revision": binding["source_head"]["owners"]["plan_revision"],
        "tasks": [{"task_id": node["key"], "node_id": node["id"], "state": node["state"]} for node in graph["nodes"]],
        "snapshot_binding": binding, "artifacts": files}


def memory_pointer(binding, graph, files):
    return {"schema": "evidence-lane.memory-locator-map.v4", "project_id": binding["project_id"],
        "locator_nodes": {node["key"]: {"node_id": node["id"], **node["locator"]} for node in graph["nodes"]},
        "snapshot_binding": binding, "artifacts": files}


def register_authority_views(engine):
    from .agent_learning import LEARNING_MIGRATIONS, learning_view
    from .canon_consequence_graph import canon_pointer, canon_view
    from .canon_task_graph import CANON_MIGRATIONS
    from .lineage import LINEAGE_MIGRATIONS, lineage_view
    from .plan_runtime import PLAN_MIGRATIONS, plan_view
    from .project_memory import MEMORY_MIGRATIONS, memory_view
    from .task_binding_registry import CONTINUATION_MIGRATIONS
    definitions = [
        LaneView("plan.dependencies", "plan", "authorities/plan", "Current Plan tasks, states and declared dependencies.", plan_view,
            ("plan",), PLAN_MIGRATIONS, "plan.mmd", "plan.dot", "plan-pointer.json", plan_pointer,
            node_kinds=('plan_task',), edge_kinds=('DEPENDS_ON',)),
        LaneView("chat_lineage.ancestry", "chat_lineage", "authorities/chat_lineage", "Visible conversation events and their recorded parent ancestry.", lineage_view,
            ("lineage",), LINEAGE_MIGRATIONS, "chat_lineage.mmd", "chat_lineage.dot", supports_query=True,
            node_kinds=('lineage_event',), edge_kinds=('PARENT_OF',)),
        LaneView("memory.links", "memory", "authorities/memory", "Attributed locators and explicitly recorded typed Memory relationships.", memory_view,
            ("memory","plan","learning","canon"), (*MEMORY_MIGRATIONS,*PLAN_MIGRATIONS,*LEARNING_MIGRATIONS,*CANON_MIGRATIONS,*LINEAGE_MIGRATIONS),
            "memory.mmd", "memory.dot", "memory-pointer.json", memory_pointer, supports_query=True, supports_history=True,
            node_kinds=('memory_locator',), edge_kinds=('DERIVED_FROM','EVIDENCES','LEARNED_FROM','MAPS_TO','RELATED_TO','REVOKES','SUPERSEDES','SUPPRESSES')),
        LaneView("learning.provenance", "learning", "authorities/learning", "Verified observations, their exact source receipts and version relationships.", learning_view,
            ("learning",), LEARNING_MIGRATIONS, "agent-learning.mmd", "agent-learning.dot", supports_query=True, supports_history=True,
            node_kinds=('learning_version','verified_exit'), edge_kinds=('VERIFIED_SOURCE_OF','PREVIOUS_VERSION')),
        LaneView("canon.consequences", "canon", "authorities/canon", "Participants, exchanges, receiver states, replies and explicit evidence references.", canon_view,
            ("canon","continuation"), (*CANON_MIGRATIONS,*CONTINUATION_MIGRATIONS),
            "canon-input.mmd", "canon-input.dot", "consequence-graph-pointer.json", canon_pointer,
            node_kinds=('canon_participant','canon_contract','canon_exchange','canon_exchange_reference','evidence_reference'),
            edge_kinds=('EXPECTS','SENT','ADDRESSES','RECEIVER_RECEIVED','RECEIVER_ADMITTED','RECEIVER_REJECTED','RECEIVER_NEEDS_CLARIFICATION','RECEIVER_SUPERSEDED','REPLIES_TO','CORRECTS','SUPERSEDES','ADDRESSES_CONTRACT','CITES',
                'REQUESTS_REVISION','DECLARES_RETURN_ROUTE','CORRECTION_TRACE')),
    ]
    for spec in definitions:
        engine.registry.register_view(spec)

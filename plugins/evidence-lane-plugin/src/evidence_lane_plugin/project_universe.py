"""Project-owned links to separately authorized roots; never a merged authority.

Attributed identities and link history live in this project's Universe SQLite.
Coherent project evidence head coordinator/lane inspection and its derived graph are in universe_snapshot;
hash-only federation lives in a separately selected coordinator's Universe.
Accepted-PV dependencies are removed, with no merged project business database.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue

from .errors import LaneError
from .migrations import Migration, apply_migrations
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import FORMAT_VERSION, LaneStore, json_text, now


class ProjectLink(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    target_project_id: str = Field(pattern=UUID_PATTERN)
    label: str = Field(min_length=1, max_length=160)
    expected_version: int = Field(default=0, ge=0)


class ProjectUnlink(Contract):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    target_project_id: str = Field(pattern=UUID_PATTERN)
    expected_version: int = Field(ge=1)


class ProjectLinkResult(Contract):
    project_id: str
    target_project_id: str
    version: int
    state: Literal["linked", "unlinked"]
    binding_digest: str
    access_granted: Literal[False] = False
    target_mutated: Literal[False] = False


class LinkedProjectsRead(Contract):
    after_project_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    limit: int = Field(default=20, ge=1, le=50)
    include_unlinked: bool = False


class LinkedProjectsPage(Contract):
    project_id: str
    links: list[dict[str, JsonValue]]
    last_project_id: str | None
    truncated: bool
    target_access_checked: Literal[False] = False


class ProjectLinksVerify(Contract):
    max_events: int = Field(default=1000, ge=1, le=10000)


class ProjectLinksVerified(Contract):
    project_id: str
    events_verified: int
    targets_verified: int
    event_head: str | None
    target_current_state_verified: Literal[False] = False
    mutation_performed: Literal[False] = False


UNIVERSE_MIGRATIONS = (Migration("universe", 1, "Attributed versioned project links without shared databases or grants", (
    """CREATE TABLE universe_links (
        target_project_id TEXT PRIMARY KEY, version INTEGER NOT NULL CHECK(version>0),
        state TEXT NOT NULL CHECK(state IN ('linked','unlinked')),
        binding_digest TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)))""",
    """CREATE TABLE universe_link_events (
        sequence INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
        actor_id TEXT NOT NULL, input_digest TEXT NOT NULL,
        body_json TEXT NOT NULL CHECK(json_valid(body_json)),
        previous_digest TEXT, digest TEXT NOT NULL UNIQUE)""",
)),)


def digest(value):
    return hashlib.sha256(json_text(value).encode()).hexdigest()


class ProjectUniverse:
    def __init__(self, store):
        self.project = store.project if isinstance(store, LaneStore) else store
        self.store = self.project.lane('universe')

    @staticmethod
    def _exists(connection):
        return connection.execute("SELECT 1 FROM sqlite_schema WHERE name='universe_links'").fetchone() is not None

    def _body(self, row):
        body = json.loads(row["body_json"])
        if (digest(body) != row["binding_digest"] or body.get("project_id") != self.store.project_id
                or any(body.get(key) != row[key] for key in ("target_project_id", "version", "state"))):
            raise LaneError("PROJECT_LINK_INTEGRITY", "The project link differs from its recorded binding.")
        return {**body, "binding_digest": row["binding_digest"]}

    def binding(self, target):
        with self.store.connection(read_only=True) as connection:
            row = connection.execute("SELECT * FROM universe_links WHERE target_project_id=?", (target.project_id,)).fetchone() if self._exists(connection) else None
            if row is None:
                raise LaneError("PROJECT_LINK_REQUIRED", "Link the explicitly selected project before requesting a linked view.")
            body = self._body(row)
            if body["state"] != "linked":
                raise LaneError("PROJECT_LINK_REQUIRED", "The selected project link is no longer active.")
            if (body["target_state_root"] != str(target.root) or body["target_source_root"] != str(target.source_root)
                    or body["target_format_version"] != FORMAT_VERSION):
                raise LaneError("PROJECT_LINK_BINDING_CHANGED", "Relink the exact selected target after its root or format changed.")
            return body

    def change(self, request, lease, *, actor_id, target=None, authorize=None):
        if request.target_project_id == self.store.project_id:
            raise LaneError("PROJECT_LINK_SELF", "Select another project for a link.")
        if lease.store.root != self.store.root or lease.store.project_id != self.store.project_id:
            raise LaneError('WRITER_PROJECT_MISMATCH', 'The Universe writer belongs to another project.')
        lease.check()
        apply_migrations(self.store, UNIVERSE_MIGRATIONS, writer=lease)
        key = digest({"request": request.model_dump(), "operation": "link" if target else "unlink"})
        if authorize is not None:
            authorize()
        with lease.transaction('universe') as connection:
            replay = connection.execute("SELECT * FROM universe_link_events WHERE request_id=?", (request.request_id,)).fetchone()
            if replay is not None:
                original = dict(replay)
                recorded_digest = original.pop("digest")
                if digest(original) != recorded_digest:
                    raise LaneError("PROJECT_LINK_HISTORY_INTEGRITY", "The stored project link event is inconsistent.")
                if replay["actor_id"] != actor_id or replay["input_digest"] != key:
                    raise LaneError("PROJECT_LINK_REQUEST_CONFLICT", "This request id belongs to different link content.")
                return ProjectLinkResult(**json.loads(replay["body_json"])["result"])
            prior = connection.execute("SELECT * FROM universe_links WHERE target_project_id=?", (request.target_project_id,)).fetchone()
            if (prior["version"] if prior else 0) != request.expected_version:
                raise LaneError("PROJECT_LINK_VERSION_CONFLICT", "Select the current project link version.")
            if prior:
                self._body(prior)
            version = request.expected_version + 1
            state = "linked" if target else "unlinked"
            if target:
                body = {"project_id": self.store.project_id, "target_project_id": target.project_id,
                    "target_state_root": str(target.root), "target_source_root": str(target.source_root),
                    "target_format_version": FORMAT_VERSION, "label": request.label}
            elif prior and prior["state"] == "linked":
                body = json.loads(prior["body_json"])
            else:
                raise LaneError("PROJECT_LINK_REQUIRED", "There is no active project link to remove.")
            body.update({"version": version, "state": state, "source_client_id": actor_id, "updated_at": now()})
            binding = digest(body)
            connection.execute("INSERT INTO universe_links VALUES(?,?,?,?,?) ON CONFLICT(target_project_id) DO UPDATE SET version=excluded.version,state=excluded.state,binding_digest=excluded.binding_digest,body_json=excluded.body_json",
                               (request.target_project_id, version, state, binding, json_text(body)))
            result = ProjectLinkResult(project_id=self.store.project_id, target_project_id=request.target_project_id,
                                       version=version, state=state, binding_digest=binding)
            previous = connection.execute("SELECT sequence,digest FROM universe_link_events ORDER BY sequence DESC LIMIT 1").fetchone()
            event = {"sequence": previous["sequence"] + 1 if previous else 1, "request_id": request.request_id,
                "actor_id": actor_id, "input_digest": key, "body_json": json_text({"binding": body, "result": result.model_dump()}),
                "previous_digest": previous["digest"] if previous else None}
            connection.execute("INSERT INTO universe_link_events VALUES(?,?,?,?,?,?,?)",
                               (*event.values(), digest(event)))
            self.store.append_receipt("project_link_changed", result.model_dump(), connection=connection)
            return result

    def read(self, request=None):
        request = request or LinkedProjectsRead()
        with self.store.connection(read_only=True) as connection:
            connection.execute("BEGIN")
            if not self._exists(connection):
                return LinkedProjectsPage(project_id=self.store.project_id, links=[], last_project_id=request.after_project_id, truncated=False)
            conditions = ["target_project_id>?"]
            if not request.include_unlinked:
                conditions.append("state='linked'")
            rows = connection.execute("SELECT * FROM universe_links WHERE " + " AND ".join(conditions) + " ORDER BY target_project_id LIMIT ?",
                                      (request.after_project_id or "", request.limit + 1)).fetchall()
            links = [self._body(row) for row in rows[:request.limit]]
            return LinkedProjectsPage(project_id=self.store.project_id, links=links,
                last_project_id=links[-1]["target_project_id"] if links else request.after_project_id,
                truncated=len(rows) > request.limit)

    def verify(self, request):
        from .migrations import read_compatibility
        read_compatibility(self.store, UNIVERSE_MIGRATIONS)
        with self.store.connection(read_only=True) as connection:
            connection.execute('BEGIN')
            rows = connection.execute('SELECT * FROM universe_link_events ORDER BY sequence LIMIT ?',
                (request.max_events + 1,)).fetchall() if self._exists(connection) else []
            if len(rows) > request.max_events:
                raise LaneError('UNIVERSE_LINK_VERIFY_BUDGET', 'Select a larger bounded link-history verification budget.')
            previous, targets = None, {}
            for number, row in enumerate(rows, 1):
                event = dict(row)
                recorded = event.pop('digest')
                body = json.loads(event['body_json'])
                binding, result = body['binding'], body['result']
                if (event['sequence'] != number or event['previous_digest'] != previous or digest(event) != recorded
                        or binding['project_id'] != self.project.project_id or result['binding_digest'] != digest(binding)
                        or any(result[key] != binding[key] for key in ('project_id', 'target_project_id', 'version', 'state'))
                        or binding['version'] != targets.get(binding['target_project_id'], {}).get('version', 0) + 1):
                    raise LaneError('PROJECT_LINK_HISTORY_INTEGRITY', 'A recorded link differs from its versioned history.')
                targets[binding['target_project_id']] = binding
                previous = recorded
            current = connection.execute('SELECT * FROM universe_links ORDER BY target_project_id LIMIT ?',
                (request.max_events + 1,)).fetchall() if self._exists(connection) else []
            if len(current) != len(targets) or any({key: value for key, value in self._body(row).items() if key != 'binding_digest'}
                    != targets.get(row['target_project_id']) for row in current):
                raise LaneError('PROJECT_LINK_HISTORY_INTEGRITY', 'Current links differ from their recorded event heads.')
            return ProjectLinksVerified(project_id=self.project.project_id, events_verified=len(rows),
                targets_verified=len(targets), event_head=previous)


def register_project_link_actions(engine):
    def link(context, request):
        from .lane_reader import LaneReader
        reader = LaneReader(engine)
        reader._context(context, request.target_project_id)
        target = engine.directory.open(request.target_project_id)
        source = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(source) as lease:
            if context.authorize:
                context.authorize('write')
            target = engine.directory.open(request.target_project_id)
            target.assert_current_binding()
            return ProjectUniverse(source).change(request, lease, actor_id=context.client_id, target=target,
                authorize=lambda: reader._context(context, request.target_project_id))

    def unlink(context, request):
        source = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(source) as lease:
            if context.authorize:
                context.authorize('write')
            return ProjectUniverse(source).change(request, lease, actor_id=context.client_id)

    engine.registry.register(ActionSpec("project_evidence_link", "Link an explicitly selected project without granting access or copying its data.",
        ProjectLink, ProjectLinkResult, link, permission="write", profile="universe", mutates=True, workflow='inspect-project-evidence-map'))
    engine.registry.register(ActionSpec("project_evidence_unlink", "Remove the current project link while retaining its recorded history.",
        ProjectUnlink, ProjectLinkResult, unlink, permission="write", profile="universe", mutates=True, workflow='inspect-project-evidence-map'))
    engine.registry.register(ActionSpec("project_evidence_links_read", "Read this project's attributed links without opening or authorizing their targets.",
        LinkedProjectsRead, LinkedProjectsPage, lambda context, request: ProjectUniverse(engine.directory.open(context.project_id)).read(request),
        profile="universe", queryable_in_delta=True, workflow='inspect-project-evidence-map'))
    engine.registry.register(ActionSpec('project_evidence_links_verify', 'Verify bounded project link history and current bindings without opening member projects.',
        ProjectLinksVerify, ProjectLinksVerified,
        lambda context, request: ProjectUniverse(engine.directory.open(context.project_id)).verify(request),
        profile='universe', queryable_in_delta=True, workflow='inspect-project-evidence-map', studio_read=True))

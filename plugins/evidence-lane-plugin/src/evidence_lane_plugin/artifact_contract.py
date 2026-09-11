"""Version-bound, lane-owned artifact publication and read-only validation.

SQLite selects the current immutable snapshot. Lane-specific navigation files
live inside that snapshot, eliminating a second mutable current-file authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, JsonValue, model_validator

from .errors import LaneError
from .lane_contract import ViewScope, content_digest
from .migrations import Migration, apply_migrations, read_compatibility
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import LaneStore, bounded_project_read, json_text, now, reject_links

DIGEST = r"^[0-9a-f]{64}$"
VIEW_ID = r"^[a-z][a-z0-9_]{0,47}\.[a-z][a-z0-9_]{0,47}$"


class ViewPreview(Contract):
    view_id: str = Field(pattern=VIEW_ID)
    scope: ViewScope = Field(default_factory=ViewScope)


class ViewPreviewResult(Contract):
    project_id: str
    view_id: str
    contract_digest: str
    source_digest: str
    source_head: dict[str, JsonValue]
    generation: int
    scope: ViewScope
    graph: dict[str, JsonValue]
    files: dict[str, str]
    files_written: Literal[False] = False


class ViewRefresh(ViewPreview):
    request_id: str = Field(default_factory=lambda: str(uuid4()), pattern=UUID_PATTERN)
    expected_generation: int = Field(ge=0)
    contract_digest: str = Field(pattern=DIGEST)
    source_digest: str = Field(pattern=DIGEST)
    formats: list[Literal["mmd", "dot"]] = Field(default_factory=lambda: ["mmd"], max_length=2)
    include_pointer: bool = False
    dot_validation: Literal["source", "native"] = "source"
    consumer: Literal["artifact_export"] = "artifact_export"

    @model_validator(mode="after")
    def selected_files(self):
        if len(set(self.formats)) != len(self.formats) or not self.formats and not self.include_pointer:
            raise ValueError("Select distinct formats or a supported navigation pointer")
        if self.dot_validation == "native" and "dot" not in self.formats:
            raise ValueError("Native DOT validation requires the DOT format")
        return self


class ViewPublished(Contract):
    project_id: str
    view_id: str
    state: Literal["published", "empty"]
    generation: int
    snapshot_digest: str | None
    files: list[dict[str, JsonValue]]
    source_authority_mutated: Literal[False] = False
    reused_snapshot: bool = False


class ViewRead(Contract):
    view_id: str = Field(pattern=VIEW_ID)
    snapshot_digest: str | None = Field(default=None, pattern=DIGEST)
    include_content: bool = False
    max_bytes: int = Field(default=131072, ge=2048, le=262144)


class ViewState(Contract):
    project_id: str
    view_id: str
    state: Literal["not_materialized", "fresh", "stale", "historical", "contract_changed"]
    generation: int
    checked_at: str = Field(default_factory=now)
    snapshot_digest: str | None = None
    manifest: dict[str, JsonValue] | None = None
    contents: dict[str, str] | None = None
    refresh_performed: Literal[False] = False


VIEWS_MIGRATIONS = (Migration("views", 1, "Lane contracts, immutable snapshot manifests and current selectors", (
    """CREATE TABLE views_contracts (contract_digest TEXT PRIMARY KEY, view_id TEXT NOT NULL,
        body_json TEXT NOT NULL CHECK(json_valid(body_json)))""",
    """CREATE TABLE views_snapshots (snapshot_digest TEXT PRIMARY KEY, view_id TEXT NOT NULL,
        generation INTEGER NOT NULL CHECK(generation>0), body_json TEXT NOT NULL CHECK(json_valid(body_json)),
        UNIQUE(view_id,generation))""",
    """CREATE TABLE views_current (view_id TEXT PRIMARY KEY, generation INTEGER NOT NULL CHECK(generation>0),
        snapshot_digest TEXT NOT NULL REFERENCES views_snapshots(snapshot_digest))""",
    """CREATE TABLE views_requests (request_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, input_digest TEXT NOT NULL,
        result_json TEXT NOT NULL CHECK(json_valid(result_json)))""",
)),)


@dataclass(frozen=True)
class SelectedViewRefresh:
    """An operation-owned dependency on one existing lane export.

    This binds the selected snapshot at admission; it does not create an export
    or turn its optional native validator into a lane-wide dependency.
    """
    engine: object
    view_id: str

    def schema(self):
        return {'view_id': self.view_id, 'selection': 'existing_consumer_exact_snapshot',
            'preserve': ['scope', 'formats', 'include_pointer', 'dot_validation'],
            'native_dot': {'tool_ids': ['Graphviz_dot'], 'engine_systems': ['Windows'],
                'client_families': ['codex_desktop', 'codex_cli', 'codex_vm']}}

    def resolve(self, context):
        from .host_routing import HOST_MATRIX
        store = self.engine.directory.open(context.project_id)
        selection = LaneArtifacts(self.engine, store).current_selection(self.view_id)
        native = bool(selection and selection['dot_validation'] == 'native')
        host = context.host_observation
        profile = host.client.configured_profile if host is not None else None
        family = HOST_MATRIX.get(profile, {}).get('family')
        return {'view_id': self.view_id, 'selection': selection,
            'tool_ids': ['Graphviz_dot'] if native else [],
            'native_dot': native, 'host_compatible': not native or family in {'codex_desktop', 'codex_cli', 'codex_vm'}}


class LaneArtifacts:
    def __init__(self, engine, store):
        self.engine, self.store = engine, store
        self.project = store.project if isinstance(store, LaneStore) else store

    def _select(self, view_id):
        spec = self.engine.registry.get_view(view_id)
        self.store = self.project.lane(spec.lane_id)
        return spec

    def _current(self, view_id):
        read_compatibility(self.store, VIEWS_MIGRATIONS)
        with self.store.connection(read_only=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='views_current'").fetchone():
                return None
            row = connection.execute("SELECT * FROM views_current WHERE view_id=?", (view_id,)).fetchone()
            return dict(row) if row else None

    def current_selection(self, view_id):
        """Capture an existing consumer's exact formats and scope for refresh."""
        spec = self._select(view_id)
        current = self._current(view_id)
        if current is None:
            return None
        manifest, _ = self._manifest(spec, current['snapshot_digest'])
        if manifest['binding']['contract_digest'] != spec.digest:
            raise LaneError('VIEW_CONTRACT_CHANGED', 'Refresh the selected view contract before changing its source.')
        return {'snapshot_digest': current['snapshot_digest'], 'generation': current['generation'],
            'scope': manifest['binding']['scope'],
            'formats': [row['role'] for row in manifest['files'] if row['role'] in {'mmd', 'dot'}],
            'include_pointer': any(row['role'] == 'pointer' for row in manifest['files']),
            'dot_validation': manifest.get('dot_validation', 'source')}

    def refresh_selected(self, view_id, selection, execution, *, actor_id):
        """Refresh inside the active Delta, preserving the selected consumer."""
        if self.current_selection(view_id) != selection:
            raise LaneError('VIEW_SELECTION_CHANGED', 'The selected lane export changed during the source operation.')
        if selection is None:
            return None
        preview = self.preview(ViewPreview(view_id=view_id, scope=selection['scope']), writer=execution.lease)
        return self.refresh(ViewRefresh(view_id=view_id, scope=preview.scope,
            expected_generation=selection['generation'], contract_digest=preview.contract_digest,
            source_digest=preview.source_digest, formats=selection['formats'],
            include_pointer=selection['include_pointer'], dot_validation=selection['dot_validation']), execution.lease,
            actor_id=actor_id, execution=execution).model_dump(mode='json')

    def verify_refreshed(self, view_id, result):
        """Verify the owning manifest/files, independently of public read caps."""
        spec = self._select(view_id)
        with bounded_project_read(self.project.root, time.monotonic() + 10):
            selected = self._current(view_id)
            if result is None or selected is None:
                return result is None and selected is None, 0
            actual, _ = self._manifest(spec, selected['snapshot_digest'])
            expected_files = [{**item, 'path': str(self._path(spec, selected['snapshot_digest'], item['filename']))}
                              for item in actual['files']]
            valid = (actual['binding']['source_head'] == spec.source_head(self.store)
                and actual['binding']['contract_digest'] == spec.digest
                and result['project_id'] == self.project.project_id and result['view_id'] == view_id
                and selected['snapshot_digest'] == result['snapshot_digest']
                and selected['generation'] == result['generation'] == actual['generation']
                and result['files'] == expected_files)
            return valid, int(any(row['role'] in {'mmd', 'dot'} for row in actual['files']))

    def preview(self, request, *, writer=None):
        spec = self._select(request.view_id)
        with bounded_project_read(self.store.root, time.monotonic() + 10, writer=writer):
            head = spec.source_head(self.store)
            graph = spec.project(self.store, request.scope)
            from .lane_traversal import validate_view_graph
            validate_view_graph(self.store.project_id, spec.schema(), request.scope.model_dump(), graph)
            if spec.source_head(self.store) != head:
                raise LaneError("VIEW_SOURCE_CHANGED", "The lane changed while its view was being read.")
            current = self._current(request.view_id)
        value = ViewPreviewResult(project_id=self.store.project_id, view_id=spec.view_id, contract_digest=spec.digest,
            source_digest=content_digest(head), source_head=head, generation=current["generation"] if current else 0,
            scope=request.scope, graph=graph, files=spec.schema()["files"])
        if len(json_text(value.model_dump()).encode()) > 262144:
            raise LaneError("VIEW_OUTPUT_BUDGET", "Reduce the view's node or edge budget.")
        return value

    @staticmethod
    def _render(graph, formats, *, native_host_profile=None):
        if not formats:
            return {}, {}
        try:
            from .graph_pipeline import SemanticGraph
        except (ImportError, ModuleNotFoundError):
            raise LaneError("VIEW_TOOL_UNAVAILABLE", "The selected graph exporter is unavailable in this runtime.") from None
        semantic = SemanticGraph("lane_view", direction="LR", analysis="required",
            native_host_profile=native_host_profile)
        for node in graph["nodes"]:
            label = node["label"] + ("\n" + node["state"] if node["state"] else "")
            semantic.add_node(node["id"], label, "source" if node["kind"] == "evidence_reference" else "semantic")
        for edge in graph["edges"]:
            semantic.add_edge(edge["source"], edge["target"], edge["kind"] + (' (reported)' if edge.get('provenance') == 'agent_report' else ''))
        contents, evidence = {}, {}
        for selected in formats:
            try:
                text, receipt = semantic.render_mermaid() if selected == "mmd" else semantic.render_dot()
            except (ValueError, RuntimeError):
                raise LaneError("VIEW_EXPORT_FAILED", "The declared graph exporter could not verify this bounded topology.") from None
            if len(text.encode()) > 524288:
                raise LaneError("VIEW_OUTPUT_BUDGET", "The selected graph export exceeded its file budget.")
            contents[selected], evidence[selected] = text.encode(), receipt
        if len({value["semantic_topology_sha256"] for value in evidence.values()}) != 1:
            raise LaneError("VIEW_SEMANTIC_MISMATCH", "Selected graph formats differ in their semantic topology.")
        return contents, evidence

    def _path(self, spec, digest, filename=None):
        if not re.fullmatch(DIGEST, digest):
            raise LaneError("VIEW_MANIFEST_INTEGRITY", "The view snapshot has an invalid content identity.")
        root = self.store.root / spec.folder / spec.view_id.split(".")[1] / digest
        path = root / filename if filename else root
        if filename and (Path(filename).name != filename or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,95}", filename)):
            raise LaneError("VIEW_PATH_INVALID", "The artifact filename is outside its lane contract.")
        reject_links(path, self.store.root)
        return path

    def _write_snapshot(self, spec, digest, files, lease):
        root = self._path(spec, digest)
        root.mkdir(parents=True, exist_ok=True)
        reject_links(root, self.store.root)
        for filename, content in files.items():
            lease.check()
            path = self._path(spec, digest, filename)
            try:
                descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if path.read_bytes() != content:
                    raise LaneError("VIEW_EXISTING_FILE_CHANGED", "An existing artifact differs; preserve it and select a new export.") from None
            else:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            if path.stat().st_size != len(content) or hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(content).digest():
                raise LaneError("VIEW_WRITE_VERIFICATION", "The artifact content did not match its staged bytes.")

    def refresh(self, request, lease, *, actor_id, execution=None):
        spec = self._select(request.view_id)
        if request.contract_digest != spec.digest or request.consumer != spec.consumer:
            raise LaneError("VIEW_CONTRACT_CHANGED", "Preview the current lane view contract before exporting it.")
        roles = spec.schema()["files"]
        selected = [*request.formats, *(["pointer"] if request.include_pointer else [])]
        if not set(selected) <= set(roles):
            raise LaneError("VIEW_FORMAT_UNSUPPORTED", "Select only files supported by this lane's consuming workflow.")
        input_digest = content_digest(request.model_dump())
        current = self._current(spec.view_id)
        with self.store.connection(read_only=True) as connection:
            exists = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='views_requests'").fetchone()
            prior = connection.execute("SELECT * FROM views_requests WHERE request_id=?", (request.request_id,)).fetchone() if exists else None
        if prior:
            if prior["input_digest"] != input_digest or prior["actor_id"] != actor_id:
                raise LaneError("VIEW_REQUEST_CONFLICT", "This request id belongs to a different export.")
            result = ViewPublished.model_validate_json(prior["result_json"])
            if result.project_id != self.store.project_id or result.view_id != spec.view_id:
                raise LaneError("VIEW_REQUEST_INTEGRITY", "The recorded export belongs to another project or view.")
            if result.snapshot_digest:
                manifest, _ = self._manifest(spec, result.snapshot_digest)
                expected_files = [{**item, 'path': str(self._path(spec, result.snapshot_digest, item['filename']))} for item in manifest['files']]
                if result.files != expected_files or result.generation != manifest['generation']:
                    raise LaneError("VIEW_REQUEST_INTEGRITY", "The recorded export differs from its immutable snapshot.")
            return result
        if (current["generation"] if current else 0) != request.expected_generation:
            raise LaneError("VIEW_GENERATION_CONFLICT", "Preview the current lane view generation before replacing it.")
        preview = self.preview(ViewPreview(view_id=request.view_id, scope=request.scope),
            writer=lease if execution is not None else None)
        if preview.source_digest != request.source_digest:
            raise LaneError("VIEW_SOURCE_CHANGED", "The authoritative lane changed after the selected preview.")
        graph = preview.graph
        if current is not None:
            try:
                manifest, _ = self._manifest(spec, current['snapshot_digest'])
            except LaneError as error:
                if error.code not in {'VIEW_ARTIFACT_MISSING', 'VIEW_ARTIFACT_CHANGED'}:
                    raise
                manifest = None  # Explicit refresh may replace damaged derived files.
            binding = manifest['binding'] if manifest is not None else {}
            if (manifest is not None and binding['contract_digest'] == spec.digest and binding['source_head'] == preview.source_head
                    and binding['scope'] == request.scope.model_dump() and manifest['graph'] == graph
                    and [row['role'] for row in manifest['files']] == selected
                    and manifest.get('dot_validation', 'source') == request.dot_validation):
                # The stored graph and every selected file have just been
                # verified. Keep their original producer evidence and date.
                result = ViewPublished(project_id=self.store.project_id, view_id=spec.view_id, state='published',
                    generation=current['generation'], snapshot_digest=current['snapshot_digest'], reused_snapshot=True,
                    files=[{**item, 'path': str(self._path(spec, current['snapshot_digest'], item['filename']))}
                        for item in manifest['files']])
                lease.check()
                if self._current(spec.view_id) != current or spec.source_head(self.store) != preview.source_head:
                    raise LaneError('VIEW_SOURCE_CHANGED', 'The selected export changed before verified reuse.')
                with lease.transaction(self.store.lane_id) as connection:
                    connection.execute('INSERT INTO views_requests VALUES(?,?,?,?)',
                        (request.request_id, actor_id, input_digest, result.model_dump_json()))
                    self.store.append_receipt('lane_view_reused', result.model_dump(), connection=connection)
                return result
        if not graph["nodes"]:
            return ViewPublished(project_id=self.store.project_id, view_id=spec.view_id, state="empty",
                generation=request.expected_generation, snapshot_digest=None, files=[])
        contents, tool_evidence, worker_evidence = {}, {}, None
        if request.formats:
            if self.engine.workers is None or 'render_lane_view' not in self.engine.workers.operations:
                raise LaneError('VIEW_WORKER_UNAVAILABLE', 'This runtime has no registered graph-export worker.')
            lease.check()
            from .host_routing import HOST_MATRIX
            host = self.engine.clients.session(actor_id).host_observation
            native_host_profile = None
            if request.dot_validation == 'native':
                native_host_profile = HOST_MATRIX[host.client.configured_profile]['family'].upper()
                if native_host_profile not in {'CODEX_DESKTOP', 'CODEX_CLI', 'CODEX_VM'}:
                    raise LaneError('VIEW_NATIVE_HOST_UNAVAILABLE', 'Native DOT validation requires a supported configured client profile.')
                if execution is not None and 'Graphviz_dot' not in execution.guard.task.permitted_tools:
                    raise LaneError('DELTA_TOOL_SCOPE', 'The adopted task must permit native Graphviz to preserve this selected export.')
            arguments = {'graph': graph, 'formats': request.formats, 'native_host_profile': native_host_profile}
            future = (execution.submit('render_lane_view', arguments) if execution is not None
                      else self.engine.workers.submit('render_lane_view', arguments))
            try:
                # A Delta owns this worker until it joins, including on a stop
                # or time budget failure; it cannot release the project early.
                completed = future.result() if execution is not None else future.result(timeout=15)
            except TimeoutError:
                future.cancel()
                raise LaneError('VIEW_EXPORT_TIMEOUT', 'Graph export exceeded its wait budget; no artifact was published.') from None
            lease.check()
            if execution is not None:
                execution.guard.observe(execution)
                execution._before_more_work()
            if completed.get('status') != 'ok':
                raise LaneError('VIEW_EXPORT_FAILED', 'The graph worker did not return a verified export.',
                                details={'worker_code': completed.get('code', 'unknown')})
            generated = completed['result']
            if generated.get('input_digest') != content_digest(arguments):
                raise LaneError('VIEW_WORKER_BINDING', 'The graph worker returned a different input binding.')
            contents = {key: value.encode() for key, value in generated['contents'].items()}
            if set(contents) != set(request.formats):
                raise LaneError('VIEW_EXPORT_FAILED', 'The graph worker returned a different file selection.')
            tool_evidence = generated['evidence']
            if set(tool_evidence) != set(contents):
                raise LaneError('VIEW_WORKER_BINDING', 'The graph worker evidence differs from its file selection.')
            for role, content in contents.items():
                proof = tool_evidence[role]
                if (len(content) > 524288 or proof.get('status') != 'PASS'
                        or proof.get('node_count') != len(graph['nodes']) or proof.get('edge_count') != len(graph['edges'])
                        or str(proof.get(role + '_sha256', '')).lower() != hashlib.sha256(content).hexdigest()):
                    raise LaneError('VIEW_WORKER_BINDING', 'The graph worker bytes or topology counts differ from their export evidence.')
                native = proof.get('native_graphviz_validation')
                if role == 'dot' and request.dot_validation == 'native' and (
                        not native or native.get('status') != 'PASS' or native.get('tool_id') != 'graphviz'
                        or native.get('host_profile') != native_host_profile
                        or native.get('input_sha256') != hashlib.sha256(content).hexdigest()):
                    raise LaneError('VIEW_WORKER_BINDING', 'Native validation must bind these exact DOT bytes and the selected client profile.')
            if len({proof['semantic_topology_sha256'] for proof in tool_evidence.values()}) != 1:
                raise LaneError('VIEW_SEMANTIC_MISMATCH', 'Selected format results differ in their semantic topology.')
            worker_evidence = {'worker_pid': completed['worker_pid'], 'loaded_modules': completed['loaded_modules'],
                'pool_generation': self.engine.workers.generation, 'identity_role': 'owned_os_tool_worker',
                'host_observation_id': host.observation_id, 'configured_host_profile': host.client.configured_profile,
                'native_task_attestation': 'not_provided'}
        binding = {"project_id": self.store.project_id, "view_id": spec.view_id, "lane_id": spec.lane_id,
            "contract_version": spec.version, "contract_digest": spec.digest, "source_head": preview.source_head,
            "source_digest": preview.source_digest, "query_digest": content_digest(request.scope.model_dump()),
            "scope": request.scope.model_dump(), "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"]),
            "truncated": graph["truncated"], "semantic_digest": content_digest(graph)}
        files = {roles[key]: contents[key] for key in request.formats}
        manifest_files = [{"role": key, "filename": roles[key], "sha256": hashlib.sha256(contents[key]).hexdigest(),
                           "bytes": len(contents[key])} for key in request.formats]
        if request.include_pointer:
            pointer = (json_text(spec.pointer(binding, graph, manifest_files)) + "\n").encode()
            if len(pointer) > 524288:
                raise LaneError("VIEW_OUTPUT_BUDGET", "The lane navigation pointer exceeds its export budget.")
            files[spec.pointer_filename] = pointer
            manifest_files.append({"role": "pointer", "filename": spec.pointer_filename,
                                   "sha256": hashlib.sha256(pointer).hexdigest(), "bytes": len(pointer)})
        generation = request.expected_generation + 1
        manifest = {"binding": binding, "generation": generation, "files": manifest_files,
            "graph": graph, "tool_evidence": tool_evidence, "worker_execution": worker_evidence,
            "dot_validation": request.dot_validation,
            "source_client_id": actor_id, "created_at": now()}
        encoded = json_text(manifest)
        if len(encoded.encode()) > 1048576:
            raise LaneError("VIEW_OUTPUT_BUDGET", "The artifact manifest exceeds its bounded storage contract.")
        snapshot = content_digest(manifest)
        lease.check()
        self._write_snapshot(spec, snapshot, files, lease)
        if spec.source_head(self.store) != preview.source_head:
            raise LaneError("VIEW_SOURCE_CHANGED", "The lane changed before its derived artifacts could be published.")
        apply_migrations(self.store, VIEWS_MIGRATIONS, writer=lease)
        file_results = [{**item, "path": str(self._path(spec, snapshot, item["filename"]))} for item in manifest_files]
        result = ViewPublished(project_id=self.store.project_id, view_id=spec.view_id, state="published",
                               generation=generation, snapshot_digest=snapshot, files=file_results)
        with lease.transaction(self.store.lane_id) as connection:
            actual = connection.execute("SELECT generation FROM views_current WHERE view_id=?", (spec.view_id,)).fetchone()
            if (actual[0] if actual else 0) != request.expected_generation:
                raise LaneError("VIEW_GENERATION_CONFLICT", "The current lane view changed before publication.")
            connection.execute("INSERT OR IGNORE INTO views_contracts VALUES(?,?,?)", (spec.digest, spec.view_id, json_text(spec.schema())))
            connection.execute("INSERT INTO views_snapshots VALUES(?,?,?,?)", (snapshot, spec.view_id, generation, encoded))
            connection.execute("INSERT INTO views_current VALUES(?,?,?) ON CONFLICT(view_id) DO UPDATE SET generation=excluded.generation,snapshot_digest=excluded.snapshot_digest",
                               (spec.view_id, generation, snapshot))
            connection.execute("INSERT INTO views_requests VALUES(?,?,?,?)", (request.request_id, actor_id, input_digest, result.model_dump_json()))
            self.store.append_receipt("lane_view_published", result.model_dump(), connection=connection)
        return result

    def _manifest(self, spec, snapshot):
        with self.store.connection(read_only=True) as connection:
            row = connection.execute("SELECT * FROM views_snapshots WHERE snapshot_digest=? AND view_id=?", (snapshot, spec.view_id)).fetchone()
            if row is None:
                raise LaneError("VIEW_SNAPSHOT_NOT_FOUND", "The selected snapshot does not belong to this lane view.")
            if len(row["body_json"].encode()) > 1048576:
                raise LaneError("VIEW_MANIFEST_INTEGRITY", "The stored manifest exceeds its contract.")
            manifest = json.loads(row["body_json"])
            binding = manifest["binding"]
            contract = connection.execute('SELECT body_json FROM views_contracts WHERE contract_digest=? AND view_id=?',
                                          (binding['contract_digest'], spec.view_id)).fetchone()
            if not contract or len(contract[0].encode()) > 131072:
                raise LaneError("VIEW_CONTRACT_INTEGRITY", "The artifact's original lane contract is missing or invalid.")
            declared = json.loads(contract[0])
            from .lane_traversal import validate_snapshot_contract
            validate_snapshot_contract(self.store.project_id, declared, manifest)
            if (content_digest(manifest) != snapshot or binding["project_id"] != self.store.project_id
                    or binding["view_id"] != spec.view_id or manifest["generation"] != row["generation"]
                    or content_digest(manifest["graph"]) != binding["semantic_digest"]):
                raise LaneError("VIEW_MANIFEST_INTEGRITY", "The stored view differs from its project and content identity.")
        contents = {}
        for item in manifest["files"]:
            path = self._path(spec, snapshot, item["filename"])
            try:
                if item["bytes"] > 524288 or path.stat().st_size != item["bytes"]:
                    raise LaneError("VIEW_ARTIFACT_CHANGED", "A derived file differs from its recorded size.")
                content = path.read_bytes()
            except FileNotFoundError:
                raise LaneError("VIEW_ARTIFACT_MISSING", "A recorded lane artifact is missing; explicitly export a new view.") from None
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise LaneError("VIEW_ARTIFACT_CHANGED", "A derived file differs from its recorded digest.")
            contents[item["role"]] = content.decode("utf-8")
        return manifest, contents

    def read(self, request):
        spec = self._select(request.view_id)
        current = self._current(spec.view_id)
        if current is None:
            if request.snapshot_digest:
                raise LaneError("VIEW_SNAPSHOT_NOT_FOUND", "This project has no recorded snapshot for this lane.")
            return ViewState(project_id=self.store.project_id, view_id=spec.view_id, state="not_materialized", generation=0)
        snapshot = request.snapshot_digest or current["snapshot_digest"]
        with bounded_project_read(self.store.root, time.monotonic() + 10):
            manifest, contents = self._manifest(spec, snapshot)
            if snapshot == current['snapshot_digest'] and manifest['generation'] != current['generation']:
                raise LaneError('VIEW_SELECTION_INTEGRITY', 'The current selector differs from the recorded generation.')
            binding = manifest["binding"]
            state = ("contract_changed" if binding["contract_digest"] != spec.digest else
                     "historical" if snapshot != current["snapshot_digest"] else
                     "fresh" if spec.source_head(self.store) == binding["source_head"] else "stale")
        if self._current(spec.view_id) != current:
            raise LaneError("VIEW_SELECTION_CHANGED", "The selected lane view changed during the read.")
        result = ViewState(project_id=self.store.project_id, view_id=spec.view_id, state=state,
            generation=manifest["generation"], snapshot_digest=snapshot, manifest=manifest,
            contents=contents if request.include_content else None)
        if len(result.model_dump_json().encode()) > request.max_bytes:
            raise LaneError("VIEW_OUTPUT_BUDGET", "Read fewer records or omit rendered content.")
        return result


class ViewCatalogRequest(Contract):
    view_ids: list[str] = Field(default_factory=list, max_length=32)


def render_lane_view_worker(arguments):
    """Pure byte generation in an owned PID worker, with no project paths or DB access."""
    contents, evidence = LaneArtifacts._render(arguments['graph'], arguments['formats'],
        native_host_profile=arguments.get('native_host_profile'))
    return {'input_digest': content_digest(arguments),
            'contents': {key: value.decode('utf-8') for key, value in contents.items()}, 'evidence': evidence}


class ViewCatalog(Contract):
    views: list[dict[str, JsonValue]]


def graph_export_routes(handler):
    from .host_routing import HOST_MATRIX
    from .tool_routes import ToolRoute

    def pointer(context, request):
        return not request.formats

    def source(context, request):
        return bool(request.formats) and request.dot_validation == 'source'

    def native(context, request):
        return 'dot' in request.formats and request.dot_validation == 'native'

    exporters = ('Python', 'LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx')
    return (ToolRoute('lane_view_refresh.pointer', handler, applicable=pointer, worker_operations=()),
        ToolRoute('lane_view_refresh.source', handler, exporters, applicable=source,
            worker_operations=('render_lane_view',), reason='Selected source export with bounded graph analysis; native DOT is not requested.'),
        ToolRoute('lane_view_refresh.native_dot', handler, (*exporters, 'Graphviz_dot'), applicable=native,
            systems=('Windows',), host_profiles=tuple(key for key in HOST_MATRIX if key != 'unknown'),
            worker_operations=('render_lane_view',), reason='Explicit native DOT validation using the configured client profile and verified shared binary.'))


def register_artifact_actions(engine):
    def catalog(context, request):
        if request.view_ids:
            return ViewCatalog(views=[engine.registry.get_view(view_id).schema() for view_id in request.view_ids])
        views = engine.registry.view_schemas()
        if context.project_id:
            from .custom_lanes import ReadRegistrations, records
            store = engine.directory.open(context.project_id)
            page = records(store, ReadRegistrations(limit=100, max_bytes=262_144))
            if page['next_offset'] is not None:
                raise LaneError('VIEW_CATALOG_INSTANCE_BUDGET', 'Select exact view IDs from the paginated Custom registration reader.')
            views.extend(engine.registry.get_view(row['lane_id'] + '.structure').schema() for row in page['rows'])
        return ViewCatalog(views=views)
    engine.registry.register(ActionSpec("lane_view_catalog", "Read implemented per-lane view semantics, selected file roles and shared tools.",
        ViewCatalogRequest, ViewCatalog, catalog, project_required=False, workflow='evi'))
    engine.registry.register(ActionSpec("lane_view_preview", "Preview a bounded lane graph and exact source binding without writing files.",
        ViewPreview, ViewPreviewResult, lambda context, request: LaneArtifacts(engine, engine.directory.open(context.project_id)).preview(request),
        profile="artifacts", queryable_in_delta=True, workflow='evi'))
    def refresh(context, request):
        store = engine.directory.open(context.project_id, write=True)
        with engine.project_work.mutation(store) as lease:
            return LaneArtifacts(engine, store).refresh(request, lease, actor_id=context.client_id)
    engine.registry.register(ActionSpec("lane_view_refresh", "Export only selected lane artifacts from an exact verified source preview.",
        ViewRefresh, ViewPublished, refresh, permission="write", profile="artifacts", mutates=True,
        worker_operations=('render_lane_view',), workflow='refresh',
        tool_routes=graph_export_routes(refresh)))
    engine.registry.register(ActionSpec("lane_view_read", "Validate recorded lane artifacts and report stale bindings without refreshing them.",
        ViewRead, ViewState, lambda context, request: LaneArtifacts(engine, engine.directory.open(context.project_id)).read(request),
        profile="artifacts", queryable_in_delta=True, workflow='evi'))

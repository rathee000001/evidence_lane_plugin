"""Explicit project binding and bounded normalization for documented Codex hooks.

The owner hook channel is distinct from ordinary MCP reports. Hook input fields
are host-reported labels; this module never turns them into native attestation.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Literal, overload

from pydantic import Field, JsonValue

from .errors import LaneError
from .lineage import ChatLineage, LineageAppendResult, LineageRecord, visible_payload
from .migrations import Migration, apply_migrations
from .plan_runtime import content_digest
from .redaction import redact
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import json_text, now, project_snapshot

ENV_BUILDER_SPARSE = 'ENV_BUILDER_SPARSE'
GOVERNED_PROJECT_FULL = 'GOVERNED_PROJECT_FULL'
CAPTURE_ENVELOPE = '__evidence_lane_capture__'
CAPTURE_MIGRATIONS = (Migration('capture', 1, 'Hash-chained project capture decisions separate from visible lineage', (
    '''CREATE TABLE capture_decisions (
       sequence INTEGER PRIMARY KEY,event_id TEXT NOT NULL UNIQUE,
       previous_digest TEXT,digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)))''',
)),)


def _project_ids(value):
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).strip().lower().replace('-', '_')
            if normalized == 'project_id' and isinstance(item, str) and item.strip():
                yield item.strip()
            elif normalized != 'source_project_id':
                yield from _project_ids(item)
    elif isinstance(value, list):
        for item in value:
            yield from _project_ids(item)


def capture_text_digest(payload):
    envelope = payload.get(CAPTURE_ENVELOPE)
    return envelope['text_digest'] if isinstance(envelope, dict) else hashlib.sha256(str(payload.get('text', '')).strip().encode()).hexdigest()


def resolve_visible_source(stored, supplied_text=None):
    """Verify resupplied visible text without persisting a conversational payload."""
    envelope = stored.get(CAPTURE_ENVELOPE)
    if supplied_text is None:
        if envelope is not None:
            raise LaneError('CAPTURE_SOURCE_RESUPPLY_REQUIRED', 'Sparse capture omitted this text. Supply the exact visible source_text for hash verification.')
        return stored
    from .lineage import MAX_VISIBLE_BYTES
    if not isinstance(supplied_text, str):
        raise LaneError('CAPTURE_SOURCE_MISMATCH', 'Supply the original visible source text.')
    clean = redact({'text': supplied_text})['text']
    raw = clean.encode()
    if len(raw) > MAX_VISIBLE_BYTES:
        raise LaneError('VISIBLE_CONTENT_TOO_LARGE', 'Use the exact bounded visible source text.')
    expected = envelope['raw_text_digest'] if envelope is not None else (
        hashlib.sha256(stored['text'].encode()).hexdigest() if isinstance(stored.get('text'), str) else None)
    if hashlib.sha256(raw).hexdigest() != expected:
        raise LaneError('CAPTURE_SOURCE_MISMATCH', 'The supplied text differs from this exact redacted capture hash.')
    return {'text': clean, 'truncated': envelope['input_truncated']} if envelope is not None else stored


class CaptureRouteAuthority:
    """Immutable project selection; decisions belong to the Receipts lane."""
    def __init__(self, project):
        self.project = project
        self.store = project.lane('receipts')

    def filter(self, payload, *, kind):
        self.project.assert_current_binding()
        if CAPTURE_ENVELOPE in payload:
            raise LaneError('CAPTURE_METADATA_RESERVED', 'Engine capture metadata cannot be supplied as visible content.')
        if any(value != self.project.project_id for value in _project_ids(payload)):
            raise LaneError('CAPTURE_PROJECT_MISMATCH', 'Active project claims must match this selected capture project.')
        route = self.project.registration['capture_route']
        # Typed receipt/control reports remain attributed to their source. A
        # payload's capture_kind/accepted/status claim cannot change this rule
        # or turn a report into a verified Delta, hard gate or artifact.
        included = route == GOVERNED_PROJECT_FULL or kind in {'receipt', 'session', 'interrupt', 'handoff'}
        hook_name = payload.get('hook_event_name')
        decision = {'capture_route': route, 'registration_digest': self.project.registration['registration_digest'],
                    'payload_included': included, 'input_digest': content_digest(payload),
                    'text_digest': capture_text_digest(payload), 'input_truncated': payload.get('truncated') is True,
                    'raw_text_digest': hashlib.sha256(payload['text'].encode()).hexdigest() if isinstance(payload.get('text'), str) else None,
                    'hook_event_name': hook_name if isinstance(hook_name, str) and hook_name in SUPPORTED_HOOK_EVENTS else None}
        return (payload if included else {CAPTURE_ENVELOPE: decision}), decision

    def record(self, decision, result, lease, *, client_id, provenance):
        apply_migrations(self.store, CAPTURE_MIGRATIONS, writer=lease)
        with lease.transaction('receipts') as connection:
            previous = connection.execute('SELECT * FROM capture_decisions ORDER BY sequence DESC LIMIT 1').fetchone()
            if previous:
                self._validate(previous)
            sequence = previous['sequence'] + 1 if previous else 1
            previous_digest = previous['digest'] if previous else None
            body = {**decision, 'schema': 'evidence-lane.capture-decision.v4', 'sequence': sequence,
                    'previous_digest': previous_digest, 'project_id': self.project.project_id,
                    'event_id': result.event_id, 'cursor': result.cursor, 'stored_payload_digest': result.payload_digest,
                    'client_id': client_id, 'provenance': provenance, 'recorded_at': now(),
                    'event_content_authorizes_work': False, 'native_task_attestation': 'not_provided'}
            digest = content_digest(body)
            connection.execute('INSERT INTO capture_decisions VALUES(?,?,?,?,?)',
                               (sequence, result.event_id, previous_digest, digest, json_text(body)))
            self.store.append_receipt('capture_policy_decision', {'event_id': result.event_id,
                'decision_digest': digest, 'payload_included': decision['payload_included']}, connection=connection)

    @staticmethod
    def _validate(row):
        try:
            body = json.loads(row['body_json'])
            valid = (isinstance(body, dict) and content_digest(body) == row['digest']
                     and all(body[key] == row[key] for key in ('sequence', 'event_id', 'previous_digest')))
        except (ValueError, KeyError, TypeError):
            valid = False
        if not valid:
            raise LaneError('CAPTURE_DECISION_INTEGRITY', 'A capture decision differs from its stored hash chain.')
        return body

    def verify(self, *, limit=50_000):
        if type(limit) is not int or not 1 <= limit <= 50_000:
            raise LaneError('CAPTURE_HISTORY_BUDGET', 'Use a bounded capture decision verification limit.')
        with project_snapshot(self.project.root):
            return self._verify(limit)

    def _verify(self, limit):
        self.project.assert_current_binding()
        with self.store.connection(read_only=True) as connection:
            if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='capture_decisions'").fetchone():
                return {'decisions_verified': 0, 'head_digest': None}
            rows = connection.execute('SELECT * FROM capture_decisions ORDER BY sequence LIMIT ?', (limit + 1,)).fetchall()
        if len(rows) > limit:
            raise LaneError('CAPTURE_HISTORY_BUDGET', 'Capture decisions exceed the selected verification limit.')
        previous = None
        lineage = self.project.lane('chat_lineage')
        for sequence, row in enumerate(rows, 1):
            body = self._validate(row)
            if (row['sequence'] != sequence or row['previous_digest'] != previous
                    or body['project_id'] != self.project.project_id
                    or body['capture_route'] != self.project.registration['capture_route']
                    or body['registration_digest'] != self.project.registration['registration_digest']):
                raise LaneError('CAPTURE_DECISION_INTEGRITY', 'Capture decisions must preserve this project binding and contiguous history.')
            with lineage.connection(read_only=True) as connection:
                event = connection.execute('SELECT * FROM lineage_events WHERE event_id=?', (row['event_id'],)).fetchone()
            if event is None or any(event[key] != body[key] for key in ('cursor', 'client_id', 'provenance')) or event['payload_digest'] != body['stored_payload_digest']:
                raise LaneError('CAPTURE_DECISION_INTEGRITY', 'Each capture decision must bind its exact attributed lineage event.')
            ChatLineage.validate_row(event)
            payload = json.loads(lineage.read_object(event['payload_digest']))
            if body['payload_included']:
                valid = CAPTURE_ENVELOPE not in payload and content_digest(payload) == body['input_digest']
            else:
                keys = ('capture_route', 'registration_digest', 'payload_included', 'input_digest',
                        'text_digest', 'input_truncated', 'raw_text_digest', 'hook_event_name')
                valid = payload == {CAPTURE_ENVELOPE: {key: body[key] for key in keys}}
            if not valid:
                raise LaneError('CAPTURE_DECISION_INTEGRITY', 'The capture decision differs from its stored content or exclusion envelope.')
            previous = row['digest']
        return {'decisions_verified': len(rows), 'head_digest': previous}

HOOK_EVENT_ORDER = ('SessionStart', 'SubagentStart', 'UserPromptSubmit', 'PreToolUse', 'PermissionRequest', 'PostToolUse',
                    'PreCompact', 'PostCompact', 'Stop', 'SubagentStop', 'Interrupt', 'SessionEnd')
SUBAGENT_OBSERVER_EVENTS = frozenset({'SubagentStart', 'SubagentStop'})
SUPPORTED_HOOK_EVENTS = frozenset(HOOK_EVENT_ORDER)
DOCUMENTATION_URL = "https://learn.chatgpt.com/docs/hooks"
_VISIBLE_TOOLS = re.compile(
    r"^(Bash|apply_patch|update_plan|create_goal|get_goal|update_goal|request_user_input|request_user_input_async|"
    r"view_image|list_mcp_resources|list_mcp_resource_templates|read_mcp_resource|mcp__.+)$")


class CaptureBind(Contract):
    reported_session_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")


class CaptureBindingResult(Contract):
    project_id: str
    reported_session_id: str
    bound: Literal[True] = True
    native_task_attestation: Literal["not_provided"] = "not_provided"
    capture_route: Literal['GOVERNED_PROJECT_FULL', 'ENV_BUILDER_SPARSE']


class HookEnvelope(Contract):
    event_id: str = Field(pattern=UUID_PATTERN)
    event: dict[str, JsonValue]


class HookCaptureResult(LineageAppendResult):
    turn_control: dict[str, JsonValue]


@dataclass(frozen=True)
class CaptureBinding:
    client_id: str
    project_id: str


@overload
def _text(event, key: str, *, required: Literal[True], limit: int = 200) -> str: ...


@overload
def _text(event, key: str, *, required: Literal[False] = False, limit: int = 200) -> str | None: ...


@overload
def _text(event, key: str, *, required: bool, limit: int = 200) -> str | None: ...


def _text(event, key: str, *, required: bool = False, limit: int = 200) -> str | None:
    value = event.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        raise LaneError("HOOK_INPUT_INVALID", "The hook input does not match the supported event schema.")
    return value


def seal_observer_identity(event: dict) -> dict:
    """Hash one bounded reported child identity before hook transport.

    Called only after selecting the documented native fields. A caller cannot
    supply the derived digest in place of the required native agent_id field.
    The engine receives this inert observation, never a child session binding.
    """
    agent_id = _text(event, 'agent_id', required=True, limit=200)
    _text(event, 'agent_type', required=True, limit=200)
    return {**{key: value for key, value in event.items() if key != 'agent_id'},
            'agent_id_sha256': hashlib.sha256(agent_id.encode()).hexdigest()}


def _bound_payload(value):
    truncated = False
    remaining = 2000

    def visit(item, depth=0):
        nonlocal truncated, remaining
        remaining -= 1
        if remaining < 0 or depth > 12:
            truncated = True
            return "[OMITTED: capture structure limit]"
        if isinstance(item, str) and len(item) > 12_000:
            truncated = True
            return item[:12_000] + "[TRUNCATED]"
        if isinstance(item, dict):
            if len(item) > 64:
                truncated = True
            return {str(key): visit(content, depth + 1) for key, content in list(item.items())[:64]}
        if isinstance(item, list):
            if len(item) > 64:
                truncated = True
            return [visit(content, depth + 1) for content in item[:64]]
        return item

    bounded = visit(redact(visible_payload(value)))
    if len(json_text(bounded).encode()) > 60_000:
        return {"content_omitted": "Visible payload exceeded the bounded capture size.", "truncated": True}
    return {**bounded, "truncated": truncated}


def normalize_hook(envelope: HookEnvelope) -> LineageRecord:
    event = envelope.event
    name = _text(event, "hook_event_name", required=True)
    if name not in SUPPORTED_HOOK_EVENTS:
        raise LaneError("HOOK_EVENT_UNSUPPORTED", "This event is outside the documented capture contract.")
    session = _text(event, "session_id", required=True, limit=128)
    turn = _text(event, "turn_id", limit=128)
    tool_id = None
    payload: dict[str, JsonValue] = {"hook_event_name": name, "model_reported": _text(event, "model", limit=200)}
    if name == "UserPromptSubmit":
        prompt = event.get("prompt")
        if not isinstance(prompt, str):
            raise LaneError("HOOK_INPUT_INVALID", "A prompt hook must provide visible prompt text.")
        kind = "prompt"
        payload["text"] = prompt
    elif name in {"PreToolUse", "PostToolUse", "PermissionRequest"}:
        tool = _text(event, "tool_name", required=True)
        tool_id = _text(event, "tool_use_id", required=name != "PermissionRequest")
        kind = "tool_result" if name == "PostToolUse" else "tool_call"
        payload["tool_name"] = tool
        # Uncatalogued tool contents are never copied speculatively. This also
        # excludes private host tooling and avoids recursively logging reads of
        # the lineage itself. Hosted WebSearch has no native hook coverage.
        if _VISIBLE_TOOLS.fullmatch(tool) and not re.search(r"__(lineage_|capture_)", tool):
            payload["tool_input"] = event.get("tool_input")
            if name == "PostToolUse":
                payload["tool_response"] = event.get("tool_response")
        else:
            payload["content_omitted"] = "Tool content is outside the visible capture allowlist."
    elif name == "Stop":
        kind = "assistant"
        message = event.get("last_assistant_message")
        if message is not None and not isinstance(message, str):
            raise LaneError("HOOK_INPUT_INVALID", "The stop hook message must be visible text or null.")
        payload.update(text=message, message_available=message is not None,
                       continued_stop=event.get("stop_hook_active") is True)
    elif name in SUBAGENT_OBSERVER_EVENTS:
        kind = 'session'
        digest = _text(event, 'agent_id_sha256', required=True, limit=64)
        if 'agent_id' in event or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise LaneError('HOOK_INPUT_INVALID', 'A child observation requires its bounded transport digest.')
        payload.update(agent_id_sha256=digest,
                       agent_type_reported=_text(event, 'agent_type', required=True, limit=200),
                       identity_provenance='reported_unverified',
                       observation_scope='bound_parent_session',
                       subagent_control_emitted=False, execution_authorized=False)
    elif name == "Interrupt":
        kind = "interrupt"
    else:
        kind = "session"
        payload.update(source=event.get("source"), reason=event.get("reason"), trigger=event.get("trigger"))
    # transcript_path, CWD, private buffers and undocumented event fields do not
    # select a project or become stored capture. No transcript file is opened.
    return LineageRecord(event_id=envelope.event_id, kind=kind, payload=_bound_payload(payload),
                         reported_session_id=session, reported_turn_id=turn, tool_use_id=tool_id)


class CaptureRouter:
    def __init__(self, engine):
        self.engine = engine
        self._bindings: dict[str, CaptureBinding] = {}
        self._lock = threading.RLock()

    @contextmanager
    def session_transition(self, *, client_id, project_id, reported_session_id,
                           previous_client_id=None, previous_session_id=None, close=False):
        """Publish a volatile capture binding only after its session commit succeeds."""
        with self._lock:
            selected = CaptureBinding(client_id, project_id)
            existing = self._bindings.get(reported_session_id)
            previous = CaptureBinding(previous_client_id, project_id) if previous_client_id else None
            if existing is not None and existing not in {selected, previous}:
                raise LaneError('CAPTURE_SESSION_ALREADY_BOUND', 'The reported session already captures another selected project.')
            if not close and existing is None and len(self._bindings) >= 256:
                raise LaneError('CAPTURE_BINDING_LIMIT', 'Close an existing capture session first.')
            yield
            if previous_session_id and self._bindings.get(previous_session_id) == previous:
                self._bindings.pop(previous_session_id, None)
            if close:
                if self._bindings.get(reported_session_id) == selected:
                    self._bindings.pop(reported_session_id, None)
            else:
                self._bindings[reported_session_id] = selected

    def session_bound(self, client_id, project_id, reported_session_id):
        with self._lock:
            return self._bindings.get(reported_session_id) == CaptureBinding(client_id, project_id)

    def detach_project(self, client_id, project_id):
        with self._lock:
            self._bindings = {key: value for key, value in self._bindings.items()
                              if value != CaptureBinding(client_id, project_id)}

    def bind(self, context, request: CaptureBind):
        session = self.engine.clients.session(context.client_id)
        self.engine.clients.context(session, context.project_id, "write")
        project = self.engine.directory.open(context.project_id)
        with self._lock:
            # Drop bindings whose local clients no longer authenticate.
            for key, value in list(self._bindings.items()):
                try:
                    self.engine.clients.session(value.client_id)
                except LaneError:
                    del self._bindings[key]
            existing = self._bindings.get(request.reported_session_id)
            selected = CaptureBinding(context.client_id, context.project_id)
            if existing is not None and existing != selected:
                raise LaneError("CAPTURE_SESSION_ALREADY_BOUND", "This reported host session already has a different active project binding.")
            if existing is None and len(self._bindings) >= 256:
                raise LaneError("CAPTURE_BINDING_LIMIT", "Close an existing capture session before creating another.")
            self._bindings[request.reported_session_id] = selected
        return CaptureBindingResult(project_id=context.project_id, reported_session_id=request.reported_session_id,
                                    capture_route=project.registration['capture_route'])

    def capture(self, envelope: HookEnvelope, *, expected_client_id=None, expected_project_id=None,
                provenance='owner_hook_channel'):
        with self.engine.admit():
            record = normalize_hook(envelope)
            reported_session = record.reported_session_id
            if reported_session is None:
                raise LaneError('HOOK_INPUT_INVALID', 'A captured hook requires its reported session identity.')
            with self._lock:
                binding = self._bindings.get(reported_session)
            if binding is None:
                raise LaneError("CAPTURE_BINDING_REQUIRED", "Connect this host session to an explicitly selected project before capture.")
            if ((expected_client_id is not None and binding.client_id != expected_client_id)
                    or (expected_project_id is not None and binding.project_id != expected_project_id)):
                raise LaneError('CAPTURE_OWNER_MISMATCH', 'This hook report does not belong to the authenticated connection and project.')
            session = self.engine.clients.session(binding.client_id)
            self.engine.clients.context(session, binding.project_id, "write")
            store = self.engine.directory.open(binding.project_id, write=True)
            with self.engine.project_work.mutation(store, kind="capture") as lease, self._lock:
                if self._bindings.get(reported_session) != binding:
                    raise LaneError('CAPTURE_BINDING_CHANGED', 'The session changed before this hook could acquire its project writer.')
                self.engine.clients.context(session, binding.project_id, 'write')
                # Every event, including terminal hooks without a prepared
                # turn, must use the same current locked policy as SDK work.
                self.engine.sessions.flash.verify(registry=self.engine.registry)
                lanes = ['chat_lineage', 'receipts']
                if record.kind == 'interrupt':
                    lanes.append('plan')
                if record.payload['hook_event_name'] == 'PreCompact':
                    with store.lane('memory').connection(read_only=True) as memory:
                        if memory.execute("SELECT 1 FROM sqlite_schema WHERE name='memory_locators'").fetchone():
                            lanes.append('memory')
                with lease.coordinated_transaction(lanes):
                    result = ChatLineage(store).append(record, lease, client_id=binding.client_id, provenance=provenance)
                    if record.kind in {'prompt','steer'}:
                        from .prompt_index import PromptIndex
                        PromptIndex(store).record_entry(result,lease,client_id=binding.client_id,registry=self.engine.registry)
                    from .codex_turn_control import TurnControl
                    turn = TurnControl(self.engine, store).observe(record, result, lease, client_id=binding.client_id)
                    if record.kind == "interrupt":
                        from .steering import Steering
                        Steering(store).record_interrupt(result.event_id, result.cursor, lease, actor_id=binding.client_id)
                return HookCaptureResult(**result.model_dump(mode='json'), turn_control=turn)


def register_capture_actions(engine):
    engine.registry.register(ActionSpec("capture_bind", "Bind this client's selected project to a reported host session for visible hook capture.",
        CaptureBind, CaptureBindingResult, engine.capture.bind, permission="write", profile="chatlineage", mutates=True, workflow='boot'))

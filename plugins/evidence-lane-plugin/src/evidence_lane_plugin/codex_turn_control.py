"""Bound visible turns and compact recovery in the original conversation owner.

Original-source decisions are retained in local qualification receipts, outside the package.
Engine capture order does not attest the native host or authorize execution.
"""
from __future__ import annotations

import json

from .errors import LaneError
from .lineage import ChatLineage
from .migrations import Migration, apply_migrations
from .plan_runtime import PlanRead, PlanStore, content_digest
from .session_authority import SessionAuthority
from .storage import json_text, project_snapshot

COMPACT_CONTEXT_BYTE_CEILING = 8192
COMPACT_SOURCES = frozenset({'auto-compact', 'compact', 'compaction', 'manual-compact', 'post-compact', 'postcompact'})
TURN_MIGRATIONS = (Migration('turn', 1, 'Source-bound visible turns and sealed compact context', (
    '''CREATE TABLE turn_events (
       sequence INTEGER PRIMARY KEY, source_event_id TEXT NOT NULL UNIQUE REFERENCES lineage_events(event_id),
       client_id TEXT NOT NULL, reported_session_id TEXT NOT NULL, reported_turn_id TEXT,
       session_digest TEXT, event_name TEXT NOT NULL, body_digest TEXT NOT NULL REFERENCES objects(digest),
       previous_digest TEXT, digest TEXT NOT NULL UNIQUE)''',
    'CREATE INDEX turn_scope ON turn_events(client_id,reported_session_id,session_digest,sequence)',
    'CREATE INDEX turn_reported_turn ON turn_events(client_id,reported_session_id,reported_turn_id,session_digest,sequence)',
)),)


def _exists(connection, table):
    return bool(connection.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (table,)).fetchone())


def _seal_compact_context_size(core):
    """Retain the original deterministic size/hash seal and 8 KiB limit."""
    result = {**core, 'serialized_bytes': 0, 'context_digest': '0' * 64}
    for _ in range(8):
        size = len(json_text(result).encode())
        if size == result['serialized_bytes']:
            break
        result['serialized_bytes'] = size
    result['context_digest'] = content_digest({k: v for k, v in result.items() if k != 'context_digest'})
    if len(json_text(result).encode()) != result['serialized_bytes'] or result['serialized_bytes'] > COMPACT_CONTEXT_BYTE_CEILING:
        raise LaneError('TURN_CONTEXT_BYTE_BUDGET', 'The sealed operating context exceeds its 8 KiB budget.')
    return result


def _validate_context(context):
    if (context.get('schema') != 'evidence-lane.turn-context.v4'
            or context.get('context_digest') != content_digest({k: v for k, v in context.items() if k != 'context_digest'})
            or context.get('serialized_bytes') != len(json_text(context).encode())
            or context['serialized_bytes'] > COMPACT_CONTEXT_BYTE_CEILING):
        raise LaneError('TURN_CONTEXT_INTEGRITY', 'The compact context differs from its bounded digest seal.')


class TurnControl:
    def __init__(self, engine, project):
        self.engine = engine
        self.project = project
        self.store = project.lane('chat_lineage')

    def _session(self, client_id, reported_session_id=None):
        with self.project.lane('receipts').connection(read_only=True) as connection:
            row = SessionAuthority.current(connection)
        if not row or row['state'] != 'active':
            return None, 'active_engine_session_missing'
        if row['owner_client_id'] != client_id or row['owner_engine_id'] != self.engine.instance_id:
            return None, 'engine_session_owner_mismatch'
        if reported_session_id is not None and row['reported_session_id'] != reported_session_id:
            return None, 'reported_session_binding_mismatch'
        return {key: row[key] for key in ('session_id', 'generation', 'event_digest', 'owner_client_id',
            'owner_engine_id', 'reported_session_id', 'flash_digest', 'runtime_package_digest')}, None

    def _plan_window(self):
        service = PlanStore(self.project)
        head = service.snapshot(PlanRead(limit=1))
        result = {key: getattr(head, key) for key in ('state', 'plan_id', 'revision', 'document_digest', 'event_head', 'total_tasks')}
        active = None
        with self.project.lane('plan').connection(read_only=True) as connection:
            if head.state == 'ready':
                rows = connection.execute("SELECT * FROM plan_tasks WHERE revision=? AND state='active' LIMIT 2", (head.revision,)).fetchall()
                if len(rows) > 1:
                    raise LaneError('TURN_MULTIPLE_ACTIVE_TASKS', 'A context requires at most one exact active Plan row.')
                active = service._view(rows[0]) if rows else None
        result.update(
            active_task_id=active.definition.task_id if active else None,
            active_task=({'task_id': active.definition.task_id, 'position': active.position,
                          'state': active.state, 'contract_digest': active.contract_digest}
                         if active else None),
            full_plan_rows_in_hook_context=False,
            full_plan_projection_action='plan_host_status',
            partial_plan_window=False,
        )
        try:
            projection = service.host_projection()
            result['host_plan_projection'] = {
                'projection_id': projection.projection_id, 'state': projection.state,
                'rows_digest': projection.rows_digest, 'row_count': projection.row_count,
                'file_sha256': projection.file_sha256, 'full_list': True,
            }
        except LaneError as error:
            if error.code != 'PLAN_PROJECTION_NOT_FOUND':
                raise
            result['host_plan_projection'] = None
        return result, active

    def _snapshot(self, client_id, reported_session_id=None, reported_turn_id=None):
        session, gap = self._session(client_id, reported_session_id)
        gaps = [gap] if gap else []
        flash = self.engine.sessions.flash.verify(registry=self.engine.registry)
        if session and (session['flash_digest'] != flash.manifest_digest or
                session['runtime_package_digest'] != self.engine.runtime_identity['source_digest']):
            gaps.append('session_package_changed')
        plan, active = self._plan_window()
        if active is None:
            gaps.append('active_plan_task_missing')
        mode = None
        if active and active.definition.mode_binding:
            from .mode_governance import validate_task_mode_binding
            validated = validate_task_mode_binding(self.project, active.definition, registry=self.engine.registry)
            mode = {'binding': active.definition.mode_binding.model_dump(mode='json'),
                    'validation_digest': validated['binding_validation_digest'],
                    'selected_mode_ids': validated['selected_mode_ids']}
        lineage = prompt = None
        with self.store.connection(read_only=True) as connection:
            if _exists(connection, 'lineage_events'):
                row = connection.execute('SELECT * FROM lineage_events ORDER BY sequence DESC LIMIT 1').fetchone()
                if row:
                    ChatLineage.validate_row(row)
                    self.store.read_object(row['payload_digest'])
                    lineage = {key: row[key] for key in ('event_id', 'sequence', 'cursor', 'payload_digest')}
            if reported_turn_id and _exists(connection, 'prompt_entries'):
                row = connection.execute('''SELECT p.* FROM prompt_entries p JOIN lineage_events l ON l.event_id=p.source_event_id
                    WHERE p.client_id=? AND p.reported_session_id=? AND l.reported_turn_id=?
                    ORDER BY p.prompt_index DESC LIMIT 1''', (client_id, reported_session_id, reported_turn_id)).fetchone()
                if row:
                    from .prompt_index import PromptIndex
                    PromptIndex._validated(row)
                    prompt = {key: row[key] for key in ('source_event_id', 'source_cursor', 'prompt_index', 'entry_digest')}
        heads = {row['lane_id']: {'revision': row['revision'], 'digest': row['head_digest']}
                 for row in self.project.lane_catalog()}
        # Published heads precede provisional capture writes. The separately
        # addressed source cursor includes the current visible hook event.
        return _seal_compact_context_size({'schema': 'evidence-lane.turn-context.v4',
            'project_id': self.project.project_id, 'client_id': client_id, 'session': session,
            'reported_session_id': reported_session_id or (session['reported_session_id'] if session else None),
            'reported_turn_id': reported_turn_id, 'flash_digest': flash.manifest_digest,
            'root_pv': self.project.pv_head(), 'lane_heads': heads,
            'published_reference_scope': 'before_context_capture_commit', 'plan': plan, 'task_mode': mode,
            'lineage_cursor': lineage, 'prompt_entry': prompt,
            'gaps': gaps, 'context_ready': not gaps, 'execution_authorized': False,
            'memory_checkpoint': None, 'memory_rehydration': 'owning_memory_checkpoint_and_rehydrate_actions',
            'host_memory': 'not_read', 'native_task_attestation': 'not_provided',
            'native_goal_completed': False, 'host_compaction_disabled': False,
            'raw_prompt_included': False, 'private_reasoning_included': False, 'full_flash_included': False})

    def _row(self, row):
        event = dict(row)
        digest = event.pop('digest')
        if content_digest(event) != digest:
            raise LaneError('TURN_EVENT_INTEGRITY', 'The turn event differs from its digest.')
        raw = self.store.read_object(row['body_digest'])
        if len(raw) > 24_576:
            raise LaneError('TURN_EVENT_BYTE_BUDGET', 'The turn record exceeds its read budget.')
        body = json.loads(raw)
        if any(body[key] != row[key] for key in ('source_event_id', 'client_id', 'reported_session_id', 'reported_turn_id', 'session_digest', 'event_name')):
            raise LaneError('TURN_EVENT_INTEGRITY', 'The turn event differs from its addressed body.')
        with self.store.connection(read_only=True) as connection:
            source = connection.execute('SELECT * FROM lineage_events WHERE event_id=?', (row['source_event_id'],)).fetchone()
        if source is None or any(source[key] != body['source'][key] for key in ('event_id', 'cursor', 'payload_digest', 'client_id', 'reported_session_id', 'reported_turn_id')):
            raise LaneError('TURN_SOURCE_INTEGRITY', 'The turn record lost its exact visible source.')
        ChatLineage.validate_row(source)
        payload = json.loads(self.store.read_object(source['payload_digest']))
        from .capture_routing import CAPTURE_ENVELOPE
        captured_event = payload.get(CAPTURE_ENVELOPE, {}).get('hook_event_name') if CAPTURE_ENVELOPE in payload else payload.get('hook_event_name')
        if captured_event != row['event_name']:
            raise LaneError('TURN_SOURCE_INTEGRITY', 'The event kind differs from its visible source.')
        if body.get('context'):
            _validate_context(body['context'])
        return body

    def _latest(self, connection, *, client_id, reported_session_id, session_digest, names, reported_turn_id=None):
        if not _exists(connection, 'turn_events'):
            return None, None
        sql = 'SELECT * FROM turn_events WHERE client_id=? AND reported_session_id=? AND session_digest IS ? AND event_name IN (' + ','.join('?' for _ in names) + ')'
        values = [client_id, reported_session_id, session_digest, *names]
        if reported_turn_id is not None:
            sql += ' AND reported_turn_id=?'
            values.append(reported_turn_id)
        row = connection.execute(sql + ' ORDER BY sequence DESC LIMIT 1', values).fetchone()
        return (row, self._row(row)) if row else (None, None)

    def _seal_compact_project_memory(self, context, lease, *, event_id, client_id):
        """Retain the original four-locator checkpoint in its own Memory lane."""
        from .project_memory import MemoryCheckpoint, MemoryRead, ProjectMemory
        with self.project.lane('memory').connection(read_only=True) as connection:
            initialized = _exists(connection, 'memory_locators')
        task_id = context['plan']['active_task_id']
        if not initialized or not task_id or not context['context_ready']:
            return context
        task = context['plan']['active_task']
        if task is None or task['task_id'] != task_id:
            raise LaneError('TURN_ACTIVE_TASK_BINDING', 'The active task changed before its Memory checkpoint.')
        service = ProjectMemory(self.project)
        page = service.read(MemoryRead(limit=4))
        ids = [item['locator_id'] for item in page.locators]
        checkpoint = service.checkpoint(MemoryCheckpoint(request_id=event_id, task_id=task_id,
            plan_revision=context['plan']['revision'], contract_digest=task['contract_digest'], locator_ids=ids),
            lease, actor_id=client_id, source_task_binding=context['session']['event_digest'])
        core = {key: value for key, value in context.items() if key not in {'context_digest', 'serialized_bytes'}}
        core['memory_checkpoint'] = {'checkpoint_digest': checkpoint.checkpoint_digest, 'locator_ids': ids,
            'source_scope': 'authenticated_engine_session', 'raw_memory_content_included': False}
        return _seal_compact_context_size(core)

    def _rehydrate_compact_project_memory(self, sealed, client_id):
        from .project_memory import MemoryRehydrate, ProjectMemory
        checkpoint = sealed.get('memory_checkpoint')
        if checkpoint is None:
            return None
        result = ProjectMemory(self.project).rehydrate(MemoryRehydrate(checkpoint_digest=checkpoint['checkpoint_digest']),
            receiver_client_id=client_id)
        if (result.checkpoint['source_client_id'] != sealed['client_id']
                or result.checkpoint['source_task_binding'] != sealed['session']['event_digest']
                or [item['locator_id'] for item in result.checkpoint['locators']] != checkpoint['locator_ids']
                or result.checkpoint['task']['key'] != sealed['plan']['active_task_id']):
            raise LaneError('TURN_MEMORY_CHECKPOINT_BINDING', 'The Memory checkpoint differs from the sealed engine session and task.')
        return {'checkpoint_digest': checkpoint['checkpoint_digest'], 'locator_ids': checkpoint['locator_ids'],
                'plan_compatible': result.plan_compatible, 'memory_head_changed': result.memory_head_changed,
                'lineage_head_changed': result.lineage_head_changed, 'raw_memory_content_included': False,
                'native_task_attestation': 'not_provided'}

    def _compatibility(self, sealed, current):
        _validate_context(sealed)
        reasons = list(current['gaps'])
        for field in ('project_id', 'client_id', 'session', 'flash_digest', 'plan', 'task_mode'):
            if sealed[field] != current[field]:
                reasons.append(field + '_changed')
        memory = self._rehydrate_compact_project_memory(sealed, current['client_id'])
        ignored = {'chat_lineage', 'receipts'}
        if memory is not None:
            ignored.add('memory')
            if memory['memory_head_changed'] or not memory['plan_compatible']:
                reasons.append('memory_checkpoint_changed')
        changed = [lane for lane, head in sealed['lane_heads'].items()
                   if lane not in ignored and current['lane_heads'].get(lane) != head]
        if changed:
            reasons.append('authority_or_sector_heads_changed')
        return {'state': 'compatible_engine_context' if not reasons else 'context_refresh_required',
                'reasons': sorted(set(reasons)), 'changed_lanes': changed,
                'sealed_context_digest': sealed['context_digest'], 'current_context_digest': current['context_digest'],
                'memory_rehydration': memory,
                'execution_authorized': False, 'native_continuity_attested': False}

    def _precompact(self, connection, compact, scope):
        digest = compact['precompact_event_digest'] if compact else None
        row = connection.execute('SELECT * FROM turn_events WHERE digest=?', (digest,)).fetchone()
        if row is None:
            raise LaneError('TURN_PRECOMPACT_MISSING', 'The completed compact record lost its exact precompact event.')
        body = self._row(row)
        if row['event_name'] != 'PreCompact' or any(row[key] != scope[key] for key in scope):
            raise LaneError('TURN_PRECOMPACT_SCOPE', 'The compact record references another session scope.')
        if compact['sealed_context_digest'] != body['context']['context_digest']:
            raise LaneError('TURN_PRECOMPACT_SCOPE', 'The compact completion references a different sealed context.')
        return row, body

    def observe(self, record, source_result, lease, *, client_id):
        """Atomically append context beside its captured visible event."""
        apply_migrations(self.store, TURN_MIGRATIONS, writer=lease)
        with lease.transaction('chat_lineage') as connection:
            previous = connection.execute('SELECT * FROM turn_events WHERE source_event_id=?', (record.event_id,)).fetchone()
            if previous:
                body = self._row(previous)
                return self._summary(previous['digest'], body)
            name = record.payload['hook_event_name']
            session, gap = self._session(client_id, record.reported_session_id)
            scope = {'client_id': client_id, 'reported_session_id': record.reported_session_id,
                     'session_digest': session['event_digest'] if session else None}
            context = self._snapshot(client_id, record.reported_session_id, record.reported_turn_id) if name in {'UserPromptSubmit', 'PreCompact', 'PostCompact', 'SessionStart'} and session else None
            if name == 'PreCompact' and context:
                context = self._seal_compact_project_memory(context, lease, event_id=record.event_id, client_id=client_id)
            gaps = [gap] if gap else []
            if name == 'UserPromptSubmit' and (not str(record.payload.get('text') or '').strip() or record.payload.get('truncated')):
                gaps.append('complete_visible_prompt_missing')
            if record.reported_turn_id is None and name in {'UserPromptSubmit', 'PreToolUse', 'PostToolUse', 'PermissionRequest', 'Stop'}:
                gaps.append('reported_turn_id_missing')
            related = compact = None
            if name in {'PreToolUse', 'PostToolUse', 'PermissionRequest', 'Stop'} and record.reported_turn_id and session:
                row, prepared = self._latest(connection, **scope, names=('UserPromptSubmit',), reported_turn_id=record.reported_turn_id)
                if row:
                    related = {'event_digest': row['digest'], 'source_event_id': row['source_event_id'],
                               'context_digest': prepared['context']['context_digest']}
                    current = self._snapshot(client_id, record.reported_session_id, record.reported_turn_id)
                    related['validation'] = self._compatibility(prepared['context'], current)
                    if prepared['gaps']:
                        gaps.append('prepared_turn_has_capture_gaps')
                else:
                    gaps.append('visible_turn_prepare_missing')
                if name == 'Stop' and not str(record.payload.get('text') or '').strip():
                    gaps.append('visible_response_missing')
            compact_reentry = name == 'SessionStart' and str(record.payload.get('source') or '').lower() in COMPACT_SOURCES
            if (name == 'PostCompact' or compact_reentry) and session:
                row, prior = self._latest(connection, **scope, names=('PreCompact', 'PostCompact'))
                expected = 'PreCompact' if name == 'PostCompact' else 'PostCompact'
                if row is None or row['event_name'] != expected or (compact_reentry and not prior.get('compact')):
                    gaps.append('matching_' + expected.lower() + '_missing')
                elif name == 'PostCompact' and row['reported_turn_id'] != record.reported_turn_id:
                    gaps.append('compact_turn_binding_mismatch')
                else:
                    if name == 'PostCompact':
                        sealed, pre_digest = prior['context'], row['digest']
                    else:
                        pre, prepared = self._precompact(connection, prior.get('compact'), scope)
                        sealed, pre_digest = prepared['context'], pre['digest']
                    compact = {**self._compatibility(sealed, context), 'precompact_event_digest': pre_digest,
                               'postcompact_event_digest': row['digest'] if compact_reentry else None,
                               'pairing_basis': 'ordered_authenticated_engine_capture'}
            body = {'source_event_id': record.event_id, **scope, 'reported_turn_id': record.reported_turn_id,
                    'event_name': name, 'source': {'event_id': source_result.event_id, 'cursor': source_result.cursor,
                    'payload_digest': source_result.payload_digest, 'client_id': client_id,
                    'reported_session_id': record.reported_session_id, 'reported_turn_id': record.reported_turn_id},
                    'context': context, 'prepared_turn': related, 'compact': compact, 'gaps': gaps,
                    'native_task_attestation': 'not_provided', 'terminal_exit': False,
                    'plan_changed': False, 'execution_authorized': False}
            prior = connection.execute('SELECT * FROM turn_events ORDER BY sequence DESC LIMIT 1').fetchone()
            if prior:
                self._row(prior)
            event = {'sequence': prior['sequence'] + 1 if prior else 1, 'source_event_id': record.event_id,
                     **scope, 'reported_turn_id': record.reported_turn_id, 'event_name': name,
                     'body_digest': self.store.put_object(json_text(body).encode(), limit=24_576),
                     'previous_digest': prior['digest'] if prior else None}
            digest = content_digest(event)
            connection.execute('INSERT INTO turn_events VALUES(:sequence,:source_event_id,:client_id,:reported_session_id,:reported_turn_id,:session_digest,:event_name,:body_digest,:previous_digest,:digest)', {**event, 'digest': digest})
            self.store.append_receipt('turn_context_observed', {'source_event_id': record.event_id, 'turn_event_digest': digest}, connection=connection)
            return self._summary(digest, body)

    @staticmethod
    def _summary(digest, body):
        return {'event_digest': digest, 'event_name': body['event_name'], 'gaps': body['gaps'],
                'context_digest': body['context']['context_digest'] if body['context'] else None,
                'prepared_turn': body['prepared_turn'], 'compact': body['compact'],
                'bounded_context': body['context'] if body['event_name'] in {'SessionStart', 'UserPromptSubmit'} else None,
                'native_task_attestation': 'not_provided', 'terminal_exit': False}

    def read(self, client_id):
        with project_snapshot(self.project.root):
            current = self._snapshot(client_id)
            session, compact = current['session'], None
            if session:
                scope = {'client_id': client_id, 'reported_session_id': session['reported_session_id'], 'session_digest': session['event_digest']}
                with self.store.connection(read_only=True) as connection:
                    row, prior = self._latest(connection, **scope, names=('PreCompact', 'PostCompact'))
                    if row and row['event_name'] == 'PostCompact' and prior.get('compact'):
                        pre, prepared = self._precompact(connection, prior['compact'], scope)
                        compact = {**self._compatibility(prepared['context'], current), 'precompact_event_digest': pre['digest'],
                                   'postcompact_event_digest': row['digest']}
                    elif row:
                        compact = {'state': 'postcompact_context_unavailable', 'latest_event_digest': row['digest']}
            return {'current': current, 'compact': compact, 'project_mutated': False,
                    'host_context_delivery': 'documented_session_start_and_prompt_hook_output',
                    'native_hook_execution_attested': False, 'execution_authorized': False}

    def verify_history(self, *, limit=10000):
        if not 1 <= limit <= 50000:
            raise LaneError('TURN_HISTORY_BUDGET', 'Select a bounded turn history verification limit.')
        with project_snapshot(self.project.root), self.store.connection(read_only=True) as connection:
            rows = connection.execute('SELECT * FROM turn_events ORDER BY sequence LIMIT ?', (limit+1,)).fetchall() if _exists(connection, 'turn_events') else []
            if len(rows) > limit:
                raise LaneError('TURN_HISTORY_BUDGET', 'Turn history exceeds the selected verification limit.')
            previous = None
            for index, row in enumerate(rows, 1):
                self._row(row)
                if row['sequence'] != index or row['previous_digest'] != previous:
                    raise LaneError('TURN_HISTORY_INTEGRITY', 'The turn history chain is inconsistent.')
                previous = row['digest']
            return {'events_verified': len(rows), 'head': previous}

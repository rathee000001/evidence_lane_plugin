"""Boot, Resume and Exit over the current engine and separate project lanes.

Preserves the original single-session and locked-Flash boundary. Removed
accepted-PV/HIL transitions have no callable route in this implementation.
"""
from __future__ import annotations

import json
from uuid import uuid4

from pydantic import JsonValue

from .errors import LaneError
from .flash_authority import FlashStatus, SessionFlashAuthority
from .plan_runtime import PlanRead, PlanStore, content_digest
from .registry import ActionSpec, Contract
from .session_authority import (
    SessionAuthority,
    SessionBoot,
    SessionBoundaryRead,
    SessionBoundaryResult,
    SessionExit,
    SessionResult,
    SessionResume,
    SessionStatusRequest,
)
from .storage import project_snapshot


class RuntimeDoctor(Contract):
    engine_phase: str
    engine_instance_id: str
    package_digest: str
    flash: FlashStatus
    host: dict[str, JsonValue]
    studio_supported: bool
    managed_toolchain: str
    native_task_attestation: str = 'not_provided'
    project_mutated: bool = False


class SessionContext(RuntimeDoctor):
    operating_context: dict[str, JsonValue]


class SessionManager:
    def __init__(self, engine):
        self.engine = engine
        self.flash = SessionFlashAuthority()

    def doctor(self, context, request):
        client = self.engine.clients.session(context.client_id)
        observation = self.engine.detector.inspect(trigger='client_connect', client=client.host_observation.client)
        return RuntimeDoctor(engine_phase=self.engine.phase, engine_instance_id=self.engine.instance_id,
            package_digest=self.engine.runtime_identity['source_digest'], flash=self.flash.verify(registry=self.engine.registry),
            host=observation.model_dump(mode='json'), studio_supported=observation.engine_studio_platform_supported,
            managed_toolchain=observation.managed_toolchain_readiness)

    def context(self, context, request):
        from .codex_turn_control import TurnControl
        doctor = self.doctor(context, request)
        project = self.engine.directory.open(context.project_id)
        operating = TurnControl(self.engine, project).read(context.client_id)
        return SessionContext(**doctor.model_dump(mode='json'), operating_context=operating)

    @staticmethod
    def _plan(project):
        plan = PlanStore(project).snapshot(PlanRead(limit=1))
        return {key: getattr(plan, key) for key in ('state', 'revision', 'document_digest', 'event_head', 'total_tasks')}

    def _read_result(self, project, record, *, operation='status', flash=None):
        values = {key: record[key] for key in ('session_id', 'state', 'generation', 'owner_client_id', 'owner_engine_id',
                  'reported_session_id', 'flash_digest', 'runtime_package_digest', 'event_digest')} if record else {'state': 'none'}
        authenticated = False
        if record and record['owner_engine_id'] == self.engine.instance_id:
            try:
                self.engine.clients.session(record['owner_client_id'])
                authenticated = True
            except LaneError:
                pass
        values.update(project_id=project.project_id, root_pv=project.pv_head(), plan=self._plan(project),
            operation=operation, owner_authenticated=authenticated,
            capture_bound=bool(record and authenticated and record['state'] == 'active' and
                self.engine.capture.session_bound(record['owner_client_id'], project.project_id, record['reported_session_id'])),
            flash_current=bool(record and flash and record['flash_digest'] == flash.manifest_digest
                and record['runtime_package_digest'] == self.engine.runtime_identity['source_digest']),
            context=flash.context if flash and record and record['state'] == 'active' else None)
        return SessionResult(**values)

    def status(self, context, request):
        from .storage_selection import StorageInspect, StorageSelection
        project = self.engine.directory.open(context.project_id)
        with project_snapshot(project.root), project.lane('receipts').connection(read_only=True) as connection:
            result = self._read_result(project, SessionAuthority.current(connection), flash=self.flash.verify(registry=self.engine.registry))
            return result.model_copy(update={'storage_route': StorageSelection(self.engine, project).inspect(
                context, StorageInspect()).model_dump(mode='json')})

    def boundary(self, context, request):
        """Inspect exact exit evidence; local records never attest native host actions."""
        from .lineage import ChatLineage
        from .task_binding_registry import ContinuationRead, TaskContinuity
        project = self.engine.directory.open(context.project_id)
        policies = self.flash.policy_rows('uop', ('uop_workflow_gate_v4',), registry=self.engine.registry)
        event = {'ordinary_turn': 'MID_DELTA_QUERY', 'delta_append': 'DELTA_EXIT',
            'session_exit': 'EXIT_BOOT', 'work_handoff': 'WORK_HANDOFF_BOUNDARY',
            'goal_completion': 'GOAL_COMPLETION_BOUNDARY'}[request.kind]
        policy = next((row for row in policies['tables']['uop_workflow_gate_v4'] if row['event_id'] == event), None)
        if policy is None:
            raise LaneError('ENV_UOP_REQUIRED_GATE_MISSING', 'The exact exit kind requires its current UOP gate.')
        source = continuation = None
        with project_snapshot(project.root), project.lane('chat_lineage').connection(read_only=True) as connection:
            if request.source_event_id is not None:
                row = connection.execute('SELECT * FROM lineage_events WHERE event_id=?', (request.source_event_id,)).fetchone() if connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name='lineage_events'").fetchone() else None
                if row is None or row['cursor'] != request.source_cursor or row['client_id'] != context.client_id:
                    raise LaneError('EXIT_SOURCE_MISMATCH', 'Use this exact client-owned visible source and cursor.')
                ChatLineage.validate_row(row)
                payload = json.loads(project.lane('chat_lineage').read_object(row['payload_digest']))
                source = {'event_id': row['event_id'], 'cursor': row['cursor'], 'payload_digest': row['payload_digest'],
                    'kind': row['kind'], 'provenance': row['provenance'], 'reported_session_id': row['reported_session_id'],
                    'reported_turn_id': row['reported_turn_id'], 'complete_visible_source': not payload.get('truncated', False),
                    'native_action_proven': False}
            if request.continuation_id is not None:
                service = TaskContinuity(project)
                if not connection.execute("SELECT 1 FROM sqlite_schema WHERE name='continuation_offers'").fetchone():
                    raise LaneError('CONTINUATION_NOT_FOUND', 'This project has no recorded continuation.')
                row, body = service._offer(connection, request.continuation_id, request.continuation_digest)
                if context.client_id not in {body['source_client_id'], body['destination_client_id']}:
                    raise LaneError('EXIT_CONTINUATION_OWNER_MISMATCH', 'The continuation must bind this source or destination client.')
                page = service.read(ContinuationRead(after_sequence=row['sequence']-1, limit=1))
                item = page.offers[0]
                continuation = {'continuation_id': item['continuation_id'], 'continuation_digest': item['continuation_digest'],
                    'state': item['state'], 'binding_digest': item['result']['binding_digest'] if item['result'] else None,
                    'identity_scope': 'authenticated_engine_clients', 'native_handoff_proven': False}
        terminal = request.kind in {'work_handoff', 'goal_completion'}
        if terminal:
            state = 'native_evidence_unavailable'
            requirement = ('Independently verified native source and destination task bindings, exact continuation and first destination receipt.'
                if request.kind == 'work_handoff' else 'Exact visible user completion decision and an independently verified native Goal completion result.')
        else:
            state = 'nonterminal_boundary'
            requirement = {'ordinary_turn': 'Keep the captured turn response separate from Delta or native Goal completion.',
                'delta_append': 'Verify and commit changed owning lanes through the Delta exit coordinator; continue the current Goal.',
                'session_exit': 'Close the exact active project session through session_exit at a quiescent boundary.'}[request.kind]
        return SessionBoundaryResult(project_id=project.project_id, kind=request.kind, policy_event=event,
            flash_digest=policies['manifest_digest'], policy=policy, source_reference=source, continuation_reference=continuation,
            evidence_state=state, next_requirement=requirement)

    @staticmethod
    def _safe_boundary(project):
        with project.lane('plan').connection(read_only=True) as plan:
            if (plan.execute("SELECT 1 FROM sqlite_schema WHERE name='jobs_jobs'").fetchone()
                    and plan.execute("SELECT 1 FROM jobs_jobs WHERE state IN ('queued','running','uncertain') LIMIT 1").fetchone()):
                raise LaneError('SESSION_WORK_NOT_QUIESCENT', 'Finish or checkpoint pending project work before changing its session.')
            if (plan.execute("SELECT 1 FROM sqlite_schema WHERE name='jobs_effects'").fetchone()
                    and plan.execute("SELECT 1 FROM jobs_effects WHERE state='prepared' LIMIT 1").fetchone()):
                raise LaneError('SESSION_EFFECT_UNCERTAIN', 'Reconcile the prepared effect before changing the project session.')
            if (plan.execute("SELECT 1 FROM sqlite_schema WHERE name='steer_requests'").fetchone()
                    and plan.execute("SELECT 1 FROM steer_requests WHERE state IN ('pending','checkpointing','ready') LIMIT 1").fetchone()):
                raise LaneError('SESSION_STEER_PENDING', 'Apply or resolve pending steers before changing the project session.')

    def _can_resume(self, project, record, context, request):
        if record['owner_client_id'] == context.client_id and record['owner_engine_id'] == self.engine.instance_id:
            return
        active = False
        if record['owner_engine_id'] == self.engine.instance_id:
            try:
                self.engine.clients.session(record['owner_client_id'])
                active = True
            except LaneError:
                pass
        if request.continuation_id is not None:
            with project.lane('chat_lineage').connection(read_only=True) as lineage:
                if not lineage.execute("SELECT 1 FROM sqlite_schema WHERE name='continuation_offers'").fetchone():
                    raise LaneError('SESSION_CONTINUATION_REQUIRED', 'The selected continuation has not been accepted.')
                row = lineage.execute('SELECT source_client_id,destination_client_id,state FROM continuation_offers WHERE continuation_id=?',
                                      (request.continuation_id,)).fetchone()
                if row is None or tuple(row) != (record['owner_client_id'], context.client_id, 'accepted'):
                    raise LaneError('SESSION_CONTINUATION_MISMATCH', 'Resume requires this exact accepted source-to-destination continuation.')
        elif active:
            raise LaneError('SESSION_OWNER_ACTIVE', 'The source client is active; complete an explicit project handoff transfer first.')
        # A disconnected/restarted engine client can be recovered using the exact
        # persisted head and current owner-granted project access, never a title/PID.

    def transition(self, action, context, request):
        if getattr(request, 'require_native_attestation', False):
            raise LaneError('NATIVE_TASK_ATTESTATION_UNAVAILABLE', 'The native host has not supplied independent task attestation.')
        client = self.engine.clients.session(context.client_id)
        flash = self.flash.verify(registry=self.engine.registry)
        project = self.engine.directory.open(context.project_id, write=True)
        authority = SessionAuthority(project)
        input_digest = content_digest(request.model_dump(mode='json'))
        with self.engine.project_work.mutation(project) as lease:
            with authority.store.connection(read_only=True) as previous_connection:
                replay = authority.replay(previous_connection, context.request_id, action, context.client_id, input_digest)
                if replay is not None:
                    # Replay is a historical result; never move the current capture binding backwards.
                    return SessionResult.model_validate(replay)
                previous = authority.current(previous_connection)
            self._safe_boundary(project)
            if action == 'session_boot':
                if previous and previous['state'] == 'active':
                    raise LaneError('ONE_SESSION_RULE_ACTIVE', 'Resume the active project session or explicitly close it first.')
                session_id, generation = str(uuid4()), 1
            else:
                if previous is None or previous['state'] != 'active':
                    raise LaneError('NO_ACTIVE_SESSION', 'There is no active project session for this transition.')
                if (request.session_id, request.expected_generation, request.expected_event_digest) != (
                        previous['session_id'], previous['generation'], previous['event_digest']):
                    raise LaneError('SESSION_HEAD_CHANGED', 'Refresh the exact session head before changing it.')
                session_id, generation = previous['session_id'], previous['generation'] + 1
                if action == 'session_resume':
                    self._can_resume(project, previous, context, request)
                elif (previous['owner_client_id'], previous['owner_engine_id']) != (context.client_id, self.engine.instance_id):
                    raise LaneError('SESSION_OWNER_REQUIRED', 'Only the current authenticated session owner may close it.')
            root_before = dict(lease.acquisition_root_pv)
            if action != 'session_exit' and request.expected_root_pv_digest != root_before['head_digest']:
                raise LaneError('SESSION_ROOT_PV_CHANGED', 'Refresh the published project evidence head coordinator before attaching the session.')
            from .storage_selection import StorageSelection
            storage_route = (StorageSelection(self.engine, project).require_current(context).model_dump(mode='json')
                if action != 'session_exit' else None)
            reported = request.reported_session_id if action != 'session_exit' else previous['reported_session_id']
            closed = action == 'session_exit'
            with self.engine.capture.session_transition(client_id=context.client_id, project_id=project.project_id,  # noqa: SIM117 - capture publishes only after the durable commit context exits
                    reported_session_id=reported, previous_client_id=previous['owner_client_id'] if previous else None,
                    previous_session_id=previous['reported_session_id'] if previous else None, close=closed):
                with lease.coordinated_transaction(['receipts']):
                    authority.initialize(lease)
                    with authority.store.transaction() as connection:
                        record = {'session_id': session_id, 'generation': generation, 'state': 'closed' if closed else 'active',
                            'owner_client_id': context.client_id, 'owner_engine_id': self.engine.instance_id,
                            'reported_session_id': reported, 'flash_digest': flash.manifest_digest,
                            'runtime_package_digest': self.engine.runtime_identity['source_digest'], 'event_digest': None}
                        body = self._read_result(project, record, operation=action, flash=flash).model_dump(mode='json')
                        body.update(capture_bound=not closed, flash_current=not closed, root_pv=root_before,
                                    host_observation=client.host_observation.model_dump(mode='json'),
                                    observation_scope='at_transition_commit',
                                    storage_route=storage_route,
                                    exit_reason=request.reason if closed else None)
                        # This project evidence head coordinator is explicitly the pre-transition reference; the
                        # receipt's own publication is available from session_status.
                        body['root_pv']['reference_scope'] = 'before_session_writer_acquisition'
                        result = authority.append(connection, action=action, client_id=context.client_id,
                            request_id=context.request_id, input_digest=input_digest, body=body)
                        return SessionResult.model_validate(result)


def register_session_actions(engine):
    manager = SessionManager(engine)
    engine.sessions = manager
    engine.registry.register(ActionSpec('runtime_doctor', 'Verify this engine, current host observations and the locked Flash routing package.',
        SessionStatusRequest, RuntimeDoctor, manager.doctor, workflow='open-project-session', project_required=False))
    engine.registry.register(ActionSpec('session_flash_status', 'Verify exact ENV/UOP policy bytes against the current registry without writing state.',
        SessionStatusRequest, FlashStatus, lambda context, request: manager.flash.verify(registry=engine.registry),
        workflow='open-project-session', project_required=False))
    engine.registry.register(ActionSpec('session_context', 'Read current bounded Plan and separate lane references, verified Flash and exact engine-scoped compact recovery.',
        SessionStatusRequest, SessionContext, manager.context, workflow='open-project-session', profile='sessions',
        queryable_in_delta=True))
    engine.registry.register(ActionSpec('session_status', 'Read the current project session, exact resume head and Plan reference.',
        SessionStatusRequest, SessionResult, manager.status, profile='sessions', workflow='open-project-session',
        queryable_in_delta=True, studio_read=True))
    engine.registry.register(ActionSpec('session_exit_boundary', 'Inspect exact local exit references and current UOP gates; unsupported native Goal or project handoff proof cannot become a terminal receipt.',
        SessionBoundaryRead, SessionBoundaryResult, manager.boundary, profile='receipts', workflow='close-project-session', queryable_in_delta=True))
    for name, model, workflow, description in (
        ('session_boot', SessionBoot, 'open-project-session', 'Boot one project session with verified Flash and attributed capture.'),
        ('session_resume', SessionResume, 'open-project-session', 'Resume the exact project session without duplicating or silently taking over a live owner.'),
        ('session_exit', SessionExit, 'close-project-session', 'Close the exact current session at a safe boundary and detach its capture binding.')):
        engine.registry.register(ActionSpec(name, description, model, SessionResult,
            lambda context, request, action=name: manager.transition(action, context, request),
            profile='sessions', workflow=workflow, permission='write', mutates=True))

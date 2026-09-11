"""Selected exports are part of source-change admission and incremental refresh."""
import os
from dataclasses import replace
from pathlib import Path

import pytest
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.host_routing import ClientHello
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.tool_routes import admission_binding

from .test_code_edit_refresh_v4 import edit_arguments, files
from .test_code_profile_v4 import call, execute
from .test_code_profile_v4 import code_system as code_system  # noqa: PLC0414
from .test_code_verification_v4 import admit
from .test_lane_artifacts_v4 import prepare, publish
from .test_lane_artifacts_v4 import views as views  # noqa: PLC0414


@pytest.fixture(autouse=True)
def shared_graph_assets(monkeypatch):
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', os.environ['EVI_GRAPH_QUALIFICATION_ASSETS'])


def configured(system, profile='codex_cli'):
    engine, store, _ = system
    _, session = engine.clients.connect(ConnectRequest(hello=ClientHello(configured_profile=profile),
        projects=[ProjectSelection(project_id=store.project_id, permissions=['read', 'write', 'tools', 'admin'])]))
    return engine, store, session


def plan(system, *, native=True):
    engine, store, _ = system
    tools = ['Python', 'SQLite_FTS5_BM25', 'Python_structural_parser',
        'LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx', *(['Graphviz_dot'] if native else [])]
    tasks = [TaskDefinition(task_id='code-' + str(i), title=action, requested_outcome='Preserve selected lane exports',
        profile='code', allowed_actions=[action], permitted_tools=tools, permitted_paths=['.'],
        acceptance_checks=list(engine.registry.get(action).verification_checks))
        for i, action in enumerate(['code_index', 'code_apply'])]
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Incremental export fixture', tasks=tasks), lease, actor_id='fixture')


def export(system, first, *, native=True, formats=None, node_limit=40):
    invoke = lambda action, arguments=None: call(system, action, arguments)
    _, request = prepare(invoke, view='local_code.relationships',
        scope={'query': first['scope_id'], 'node_limit': node_limit, 'edge_limit': 60},
        formats=['mmd', 'dot'] if formats is None else formats, include_pointer=True,
        dot_validation='native' if native else 'source')
    return publish(invoke, request)


@pytest.mark.parametrize('failure', ['tool_scope', 'binary', 'client', 'worker', 'dependency'])
def test_native_refresh_prerequisites_fail_before_source_or_lane_effects(code_system, monkeypatch, tmp_path, failure):
    system = configured(code_system)
    engine, store, _ = system
    plan(system, native=failure != 'tool_scope')
    first = execute(system)
    export(system, first)
    if failure == 'binary':
        monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', str(tmp_path / 'missing-native-root'))
    elif failure == 'client':
        system = code_system  # The original connection explicitly has profile unknown.
    elif failure == 'worker':
        engine.workers.operations.pop('render_lane_view')
    elif failure == 'dependency':
        import importlib.util
        original = importlib.util.find_spec
        monkeypatch.setattr(importlib.util, 'find_spec', lambda name, *a, **kw:
            None if name == 'langgraph' else original(name, *a, **kw))
    before = files(store.source_root), files(store.root)
    operations = engine.workers.status()['succeeded_operations']
    response = admit(system, 'code_apply', edit_arguments(system, first), index=1)
    assert response.status == 'error'
    assert response.error.code == {'worker': 'WORKER_OPERATION_UNAVAILABLE',
        'dependency': 'DEPENDENCY_UNAVAILABLE'}.get(failure, 'TOOL_ROUTE_UNAVAILABLE')
    if failure in {'tool_scope', 'binary', 'client'}:
        attempt = next(row for row in response.error.details['attempts'] if row['input_supported'])
        assert attempt['reason'] == {'tool_scope': 'DELTA_TOOL_SCOPE', 'binary': 'DEPENDENCIES_UNAVAILABLE',
            'client': 'HOST_PROFILE_UNSUPPORTED'}[failure]
        assert attempt['view_refresh']['selection']['dot_validation'] == 'native'
        assert not attempt['adapter_invoked']
    assert (files(store.source_root), files(store.root)) == before
    assert engine.workers.status()['succeeded_operations'] == operations


@pytest.mark.parametrize('mode', ['native', 'source', 'pointer'])
def test_edit_preserves_exact_consumer_and_records_selected_tool_admission(code_system, mode):
    system = configured(code_system)
    engine, store, _ = system
    plan(system, native=mode == 'native')
    first = execute(system)
    initial = export(system, first, native=mode == 'native', formats=[] if mode == 'pointer' else ['mmd', 'dot'])
    resolution = call(system, 'toolchain_resolve', {'action': 'code_apply', 'arguments': edit_arguments(system, first)})
    assert resolution.status == 'ok', resolution.error
    selected = next(row for row in resolution.result['resolution']['attempts'] if row['ready'])
    assert any(row['tool_id'] == 'Graphviz_dot' for row in selected['observations']) == (mode == 'native')
    assert selected['view_refresh']['selection']['snapshot_digest'] == initial['snapshot_digest']
    if mode == 'pointer':
        engine.workers.operations.pop('render_lane_view')
    source_before = files(store.root / 'sectors/github_code')
    result = execute(system, 'code_apply', edit_arguments(system, first), index=1)
    refreshed = result['view_refresh']
    assert refreshed['generation'] == initial['generation'] + 1 and not refreshed['reused_snapshot']
    read = call(system, 'lane_view_read', {'view_id': 'local_code.relationships'}).result
    manifest = read['manifest']
    assert read['state'] == 'fresh'
    assert manifest['binding']['scope'] == {'query': first['scope_id'], 'node_limit': 40, 'edge_limit': 60,
        'include_history': False}
    assert [row['role'] for row in manifest['files']] == (['pointer'] if mode == 'pointer' else ['mmd', 'dot', 'pointer'])
    assert manifest['dot_validation'] == ('native' if mode == 'native' else 'source')
    if mode != 'pointer':
        native = manifest['tool_evidence']['dot']['native_graphviz_validation']
        assert (native is not None) == (mode == 'native')
        if native is not None:
            assert native['status'] == 'PASS' and native['host_profile'] == 'CODEX_CLI'
    assert all(Path(row['path']).is_relative_to(store.lane('local_code').folder) for row in refreshed['files'])
    assert files(store.root / 'sectors/github_code') == source_before


def test_same_format_selection_change_invalidates_admission_before_invocation(code_system):
    system = configured(code_system)
    engine, store, session = system
    plan(system)
    first = execute(system)
    export(system, first)
    context = engine.clients.context(session, store.project_id, 'write')
    spec = engine.registry.get('code_apply')
    arguments = spec.input_model.model_validate(edit_arguments(system, first))
    selected = engine.registry.tool_router.resolve(spec, context, arguments=arguments)
    bound = admission_binding(selected)
    export(system, first, node_limit=30)
    before = files(store.source_root), files(store.root)
    with pytest.raises(LaneError) as error:
        engine.registry.tool_router.invoke(spec, replace(context, tool_admission=bound), arguments)
    assert error.value.code == 'TOOL_ADMISSION_CHANGED'
    assert (files(store.source_root), files(store.root)) == before


@pytest.mark.parametrize('formats', [['mmd'], ['mmd', 'dot'], []])
def test_unchanged_export_reuses_verified_files_and_original_worker_evidence(views, monkeypatch, formats):
    engine, _store, invoke = views
    _, initial_request = prepare(invoke, formats=formats, include_pointer=True)
    initial = publish(invoke, initial_request)
    original = invoke('lane_view_read', {'view_id': initial_request.view_id}).result['manifest']
    before = {row['path']: Path(row['path']).read_bytes() for row in initial['files']}
    operations = engine.workers.status()['succeeded_operations']
    _, request = prepare(invoke, formats=formats, include_pointer=True)
    def forbidden(*args, **kwargs):
        raise AssertionError('Unchanged export started another worker')
    monkeypatch.setattr(engine.workers, 'submit', forbidden)
    reused = publish(invoke, request)
    assert reused['reused_snapshot'] and reused['snapshot_digest'] == initial['snapshot_digest']
    assert reused['generation'] == initial['generation'] and reused['files'] == initial['files']
    assert publish(invoke, request) == reused
    assert invoke('lane_view_read', {'view_id': request.view_id}).result['manifest'] == original
    assert {path: Path(path).read_bytes() for path in before} == before
    assert engine.workers.status()['succeeded_operations'] == operations
    changed = request.model_copy(update={'include_pointer': False}) if formats else request.model_copy(update={'formats': ['mmd']})
    assert invoke('lane_view_refresh', changed.model_dump(mode='json')).error.code == 'VIEW_REQUEST_CONFLICT'


def test_damaged_derived_file_requires_real_regeneration(views):
    _, _, invoke = views
    _, request = prepare(invoke)
    initial = publish(invoke, request)
    Path(initial['files'][0]['path']).write_text('damaged')
    _, next_request = prepare(invoke)
    refreshed = publish(invoke, next_request)
    assert not refreshed['reused_snapshot'] and refreshed['generation'] == 2
    assert invoke('lane_view_read', {'view_id': request.view_id}).result['state'] == 'fresh'


def test_catalog_declares_conditional_validator_for_every_source_change_owner(views):
    from evidence_lane_plugin.tool_catalog import snapshot
    engine, _, _ = views
    catalog = snapshot({'tools': []}, registry=engine.registry)
    graphviz = next(row for row in catalog['entries'] if row['tool_id'] == 'Graphviz_dot')
    bindings = [row for row in graphviz['operation_bindings'] if row.get('condition')]
    assert {row['action'] for row in bindings} == {'code_apply', 'document_export', 'presentation_export',
        'spreadsheet_export', 'data_export', 'tableau_export', 'powerbi_export', 'pdf_export', 'media_export'}
    assert all(row['condition']['dot_validation'] == 'native' and not row['execution_verified_by_catalog'] for row in bindings)

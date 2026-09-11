from dataclasses import replace

import pytest
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.registry import ActionContext, ActionRegistry, ActionSpec, Contract
from evidence_lane_plugin.sdk import ActionRequest, dispatch
from evidence_lane_plugin.tool_routes import ToolRoute, ToolRouter, _digest, operation_contract


class Input(Contract):
    text: str


class Output(Contract):
    length: int


def primary(context, request):
    return {'length': len(request.text)}


def alternate(context, request):
    return Output(length=sum(1 for _ in request.text))


def fixture(*, handler=primary, fallback=alternate, observer=None, system='Windows', mutates=False):
    registry = ActionRegistry()
    spec = ActionSpec('measure', 'Measure text length.', Input, Output, handler,
        permission='write' if mutates else 'read', mutates=mutates,
        tool_routes=(ToolRoute('measure.primary', handler, ('Pillow',)),
                     ToolRoute('measure.fallback', fallback)))
    registry.register(spec)
    registry.tool_router = ToolRouter(observer=observer or (lambda tool, context: {
        'tool_id': tool, 'ready': tool == 'Python', 'basis': 'fixture_probe'}), system=lambda: system)
    return registry, spec, ActionContext('test-client', 'test-project', frozenset({'read', 'write'}))


def test_selected_fallback_executes_equivalent_adapter_and_hash_binds_evidence():
    registry, spec, context = fixture()
    result, receipt = registry.execute_attributed('measure', {'text': 'a😀e\n'}, context)
    assert result == {'length': 4}
    assert receipt['selection']['selected_route'] == 'measure.fallback'
    assert receipt['selection']['fallback_used']
    assert receipt['adapter'].endswith(':alternate')
    assert receipt['output_digest'] == _digest(result)
    assert receipt['input_digest'] == _digest({'text': 'a😀e\n'})
    assert receipt['receipt_digest'] == _digest({k: v for k, v in receipt.items() if k != 'receipt_digest'})
    assert receipt['adapter_source_sha256']
    assert not receipt['dependency_execution_claimed']
    assert not receipt['native_host_tool_attested']
    assert operation_contract(spec)['routes'][0]['tool_ids'] == ['Pillow']


def test_readiness_inspection_does_not_execute_or_authorize():
    def should_not_run(context, request):
        pytest.fail('Resolution must never execute a handler')
    registry, spec, context = fixture(handler=should_not_run, fallback=should_not_run)
    resolution = registry.tool_router.resolve(spec, context)
    assert resolution['selected_route'] == 'measure.fallback'
    assert resolution['execution_authorized'] is False
    assert resolution['installation_verified'] is False
    assert all(not row['adapter_invoked'] for row in resolution['attempts'])


def test_context_selection_cannot_weaken_readiness_or_choose_undeclared_workers():
    registry, spec, context = fixture()
    routed = replace(spec, worker_operations=('fixture_worker',), tool_routes=(
        ToolRoute('measure.context', primary, applicable=lambda context, request: request.text == 'selected',
            worker_operations=('fixture_worker',)),))
    registry = ActionRegistry()
    registry.register(routed)
    router = ToolRouter(observer=lambda tool, context: {'ready': True}, system=lambda: 'Windows')
    assert router.resolve(routed, context)['selected_route'] is None
    assert router.resolve(routed, context, arguments=Input(text='other'))['selected_route'] is None
    assert router.resolve(routed, context, arguments=Input(text='selected'))['selected_route'] == 'measure.context'
    invalid = replace(routed, tool_routes=(replace(routed.tool_routes[0], worker_operations=('unowned',)),))
    with pytest.raises(LaneError) as error:
        ActionRegistry().register(invalid)
    assert error.value.code == 'TOOL_ROUTE_INVALID'
    invalid = replace(routed, tool_routes=(replace(routed.tool_routes[0], applicable=lambda context, request: 'yes'),))
    with pytest.raises(LaneError) as error:
        router.resolve(invalid, context, arguments=Input(text='selected'))
    assert error.value.code == 'TOOL_ROUTE_INVALID'


@pytest.mark.parametrize('mutates', [False, True])
def test_failure_after_invocation_never_runs_an_alternate(mutates):
    called = []
    def fail(context, request):
        called.append('first')
        raise LaneError('UNCERTAIN_EFFECT', 'Reconcile the operation.')
    def second(context, request):
        called.append('second')
        return {'length': 1}
    registry, _, context = fixture(handler=fail, fallback=second, mutates=mutates,
        observer=lambda tool, context: {'ready': True})
    with pytest.raises(LaneError) as failure:
        registry.execute('measure', {'text': 'x'}, context)
    assert failure.value.code == 'UNCERTAIN_EFFECT'
    assert failure.value.details['tool_execution']['outcome'] == 'failed'
    assert called == ['first']


def test_invalid_result_is_a_failure_instead_of_a_fallback():
    registry, _, context = fixture(handler=lambda context, request: {'length': 'not-an-integer'},
        observer=lambda tool, context: {'ready': True})
    with pytest.raises(LaneError, match='output contract') as failure:
        registry.execute('measure', {'text': 'x'}, context)
    assert failure.value.code == 'INVALID_RESULT'
    assert not failure.value.details['tool_execution']['selection']['fallback_used']


def test_vendor_failure_is_redacted_and_does_not_select_a_fallback():
    def vendor(context, request):
        raise RuntimeError('secret-vendor-input-token')
    registry, _, context = fixture(handler=vendor, observer=lambda tool, context: {'ready': True})
    with pytest.raises(LaneError) as failure:
        registry.execute('measure', {'text': 'x'}, context)
    assert failure.value.code == 'TOOL_ADAPTER_FAILED'
    assert 'secret-vendor-input-token' not in str(failure.value.public())
    assert failure.value.details['tool_execution']['selection']['selected_route'] == 'measure.primary'


def test_fallback_cannot_escape_exact_delta_tool_scope():
    registry, spec, context = fixture()
    resolution = registry.tool_router.resolve(spec, context, permitted_tools=('Pillow',))
    assert resolution['selected_route'] == 'measure.fallback'  # Python is the engine interpreter.
    replacement = replace(spec, tool_routes=(spec.tool_routes[0],
        ToolRoute('measure.fallback', alternate, ('Python', 'HTTPX'))))
    resolution = registry.tool_router.resolve(replacement, context, permitted_tools=('Pillow',))
    assert resolution['selected_route'] is None
    assert resolution['attempts'][1]['reason'] == 'DELTA_TOOL_SCOPE'


def test_platform_mismatch_and_unavailable_dependencies_are_distinct():
    registry, spec, context = fixture(system='unsupported')
    resolution = registry.tool_router.resolve(spec, context)
    assert resolution['selected_route'] is None
    assert all(row['reason'] == 'HOST_PLATFORM_UNSUPPORTED' for row in resolution['attempts'])


def test_revocation_during_observation_blocks_before_effects():
    called, revoked = [], []
    def probe(tool, context):
        revoked.append(True)
        return {'ready': True}
    def authorize(permission):
        if revoked:
            raise LaneError('CLIENT_SESSION_EXPIRED', 'Reconnect.')
    registry, _, context = fixture(handler=lambda context, request: called.append(1), observer=probe)
    with pytest.raises(LaneError) as failure:
        registry.execute('measure', {'text': 'x'}, replace(context, authorize=authorize))
    assert failure.value.code == 'CLIENT_SESSION_EXPIRED'
    assert called == []


def test_client_cannot_replace_adapter_or_submit_argv():
    registry, _, context = fixture()
    with pytest.raises(LaneError) as failure:
        registry.execute('measure', {'text': 'x', 'route_id': 'my.module:run', 'argv': ['sh']}, context)
    assert failure.value.code == 'INVALID_ARGUMENTS'


def test_sdk_envelope_carries_adapter_execution_without_changing_operation_output():
    registry, _, context = fixture()
    response = dispatch(registry, ActionRequest(action='measure', arguments={'text': 'xyz'}),
                        replace(context, project_id=None))
    assert response.error.code == 'PROJECT_REQUIRED'
    registry._actions['measure'] = replace(registry.get('measure'), project_required=False)
    response = dispatch(registry, ActionRequest(action='measure', arguments={'text': 'xyz'}),
                        replace(context, project_id=None))
    assert response.status == 'ok' and response.result == {'length': 3}
    assert response.tool_execution['request_id'] == response.request_id
    assert not response.tool_execution['selection']['execution_authorized']


def test_default_route_is_operation_scoped_and_not_an_automatic_lane_bundle():
    spec = ActionSpec('read', 'Read.', Input, Output, primary, profile='docs')
    assert operation_contract(spec)['routes'][0]['tool_ids'] == ['Python']
    assert operation_contract(spec)['no_fallback_reason']
    assert operation_contract(spec)['fallback_after_invocation'] is False


def test_argument_specific_pipeline_is_not_selected_without_actual_inputs():
    registry, spec, context = fixture()
    routed = replace(spec, tool_routes=(
        ToolRoute('measure.specific', primary, argument_values=(('text', ('known',)),)),
        ToolRoute('measure.generic', alternate)))
    router = registry.tool_router
    unresolved = router.resolve(replace(routed, tool_routes=routed.tool_routes[:1]), context)
    assert unresolved['selected_route'] is None
    assert unresolved['attempts'][0]['reason'] == 'OPERATION_ARGUMENTS_REQUIRED'
    generic = router.resolve(routed, context)
    assert generic['selected_route'] == 'measure.generic' and not generic['fallback_used']
    selected = router.resolve(routed, context, arguments=Input(text='known'))
    assert selected['selected_route'] == 'measure.specific'
    assert selected['context']['arguments_sha256'] == _digest({'text': 'known'})


def test_complete_pipeline_and_host_profile_are_checked_before_tool_observation():
    from evidence_lane_plugin.host_routing import ClientHello, HostDetector

    observed = []
    registry, spec, context = fixture(observer=lambda tool, context: observed.append(tool) or {
        'ready': tool != 'HTTPX'})
    routed = replace(spec, tool_routes=(
        ToolRoute('measure.desktop', primary, ('Python', 'Pillow', 'HTTPX'),
            host_profiles=('codex_desktop',)),
        ToolRoute('measure.generic', alternate)))
    host = HostDetector(which=lambda command: None).inspect(trigger='client_connect',
        client=ClientHello(configured_profile='codex_cli'))
    cli = registry.tool_router.resolve(routed, replace(context, host_observation=host))
    assert observed == ['Python']
    assert cli['attempts'][0]['reason'] == 'HOST_PROFILE_UNSUPPORTED'
    assert cli['context']['configured_host_profile'] == 'codex_cli'
    assert cli['context']['host_profile_attestation'] == 'unavailable'
    assert cli['context']['host_profile_basis'] == 'authenticated_client_report'
    observed.clear()
    desktop = host.model_copy(update={'client': ClientHello(configured_profile='codex_desktop')})
    selection = registry.tool_router.resolve(routed, replace(context, host_observation=desktop))
    assert observed == ['Python', 'Pillow', 'HTTPX', 'Python']
    assert selection['attempts'][0]['reason'] == 'DEPENDENCIES_UNAVAILABLE'
    assert selection['selected_route'] == 'measure.generic' and selection['fallback_used']
    assert not selection['execution_authorized']


@pytest.mark.parametrize('change', ['retired_tool', 'unknown_tool', 'unsupported_host'])
def test_unowned_tool_or_host_cannot_be_published_as_a_registered_route(change):
    _, spec, _ = fixture()
    replacement = {'tool_ids': ('Package_sealer',)} if change == 'retired_tool' else {
        'tool_ids': ('invented_dependency',)} if change == 'unknown_tool' else {
        'host_profiles': ('invented_host',)}
    with pytest.raises(LaneError) as failure:
        ActionRegistry().register(replace(spec, tool_routes=(replace(spec.tool_routes[0], **replacement),)))
    assert failure.value.code == 'TOOL_ROUTE_INVALID'


def test_tool_aliases_resolve_the_same_exact_task_scope():
    registry, spec, context = fixture()
    routed = replace(spec, tool_routes=(ToolRoute('measure.aliases', primary, ('hashlib', 'sqlite3')),))
    selection = registry.tool_router.resolve(routed, context, permitted_tools=('hashlib_pathlib', 'SQLite_CAS'))
    assert selection['attempts'][0]['conditions']['task_tool_scope']
    denied = registry.tool_router.resolve(routed, context, permitted_tools=('hashlib_pathlib',))
    assert denied['attempts'][0]['reason'] == 'DELTA_TOOL_SCOPE'


@pytest.mark.parametrize('profile', ['spreadsheet', 'data_excel', 'data', 'tableau', 'power_bi', 'custom'])
def test_current_data_profiles_keep_data_class_fallback_policy(profile):
    from evidence_lane_plugin.codex_action_plane import classify_action_workflow_classes

    assert classify_action_workflow_classes({'profile': profile, 'workflow': 'build'}) == ['DATA']


def test_shared_matrix_covers_every_retained_tool_without_claiming_an_installation():
    import hashlib
    from pathlib import Path

    from evidence_lane_plugin.tool_catalog import snapshot
    plugin = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'
    catalog = snapshot({'tools': []})
    retained = [row for row in catalog['entries'] if row['lifecycle'] == 'retained']
    assert len(retained) == 103
    assert 'ENV_UOP_classifier' in {row['tool_id'] for row in retained}
    assert {row['tool_id'] for row in retained if row.get('addition_reason')} == {
        'LibreOffice', 'JSONSchema', 'PowerBI_TOM', 'PBIXRay', 'ReportLab'}
    for row in retained:
        requirement = row['installation_requirement']
        assert requirement['tool_id'] == row['tool_id']
        assert requirement['installation_scope'] == 'shared_once_all_projects'
        assert not requirement['automatic_per_lane_installation']
        assert requirement['installation_state'] == 'not_verified'
        for package in requirement['packages']:
            assert requirement['installation_required_for_windows_bundle']
            assert package['version'] and package['hashes'] and package['lock_sha256']
            assert hashlib.sha256((plugin / package['lock_path']).read_bytes()).hexdigest() == package['lock_sha256']
    retained[0]['installation_requirement']['installation_state'] = 'forged'
    assert all(row['installation_requirement']['installation_state'] == 'not_verified'
               for row in snapshot({'tools': []})['entries'] if row['lifecycle'] == 'retained')
    assert catalog['shared_bundle_ready'] is False


def test_catalog_reconciles_direct_bindings_and_pin_differences_without_execution_claims(monkeypatch):
    import importlib.metadata

    from evidence_lane_plugin.tool_catalog import snapshot
    registry, _, _ = fixture()
    real = importlib.metadata.version
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: '0.0.1' if name.casefold() == 'pillow' else real(name))
    catalog = snapshot({'tools': []}, registry=registry)
    pillow = next(row for row in catalog['entries'] if row['tool_id'] == 'Pillow')
    assert pillow['operation_bindings'][0]['action'] == 'measure'
    assert pillow['operation_bindings'][0]['route_id'] == 'measure.primary'
    assert pillow['package_pin_observations'][0]['state'] == 'differs_from_pin'
    assert not pillow['package_pin_observations'][0]['loaded_module_or_file_hash_attested']
    unused = next(row for row in catalog['entries'] if row['tool_id'] == 'HTTPX')
    assert unused['operation_binding_basis'] == 'no_direct_operation_binding'
    assert unused['operation_bindings'] == []
    assert not catalog['shared_bundle_ready']


def test_toolchain_queries_use_authenticated_api_and_keep_project_lanes_unchanged(tmp_path):
    from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
    from evidence_lane_plugin.engine import Engine
    from evidence_lane_plugin.local_transport import LocalEndpoint, LocalTransport
    from evidence_lane_plugin.sdk import EvidenceLaneClient

    with Engine(tmp_path / 'runtime') as engine, LocalEndpoint(engine):
        source = tmp_path / 'source'
        source.mkdir()
        entry = engine.directory.register(tmp_path / 'project', source_root=source, create=True, read_only=False)
        project = engine.directory.open(entry['project_id'])
        connection = ConnectRequest(projects=[ProjectSelection(project_id=project.project_id, permissions=['read'])])
        with LocalTransport(engine.root, connection=connection) as transport:
            before = {path: path.read_bytes() for path in project.root.rglob('*.sqlite*') if path.is_file()}
            client = EvidenceLaneClient(transport)
            catalog = client.call('toolchain_catalog')
            assert catalog.status == 'ok', catalog.error
            assert len(catalog.result['operations']) == len(engine.registry.schemas())
            assert catalog.result['tools']['counts']['retained'] == 103
            bound_python = next(row for row in catalog.result['tools']['entries'] if row['tool_id'] == 'Python')
            assert any(row['action'] == 'project_status' for row in bound_python['operation_bindings'])
            assert catalog.tool_execution['outcome'] == 'returned_validated_result'
            resolved = client.call('toolchain_resolve', project_id=project.project_id, arguments={'action': 'project_status'})
            assert resolved.status == 'ok', resolved.error
            assert resolved.result['resolution']['selected_route'] == 'project_status.engine'
            assert resolved.result['resolution']['execution_authorized'] is False
            selection = resolved.result['resolution']['context']
            assert selection['project_id'] == project.project_id
            assert selection['host_profile_basis'] == 'authenticated_client_report'
            assert selection['host_profile_attestation'] == 'unavailable'
            assert resolved.tool_execution['env_uop']['selection_context']['project_id'] == project.project_id
            denied = client.call('toolchain_resolve', project_id=project.project_id, arguments={'action': 'plan_create'})
            assert denied.error.code == 'PERMISSION_DENIED'
            after = {path: path.read_bytes() for path in project.root.rglob('*.sqlite*') if path.is_file()}
            assert before == after

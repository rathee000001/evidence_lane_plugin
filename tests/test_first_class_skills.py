"""Behavioral and standalone-package verification for the v4 skill surface."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import shutil
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest
from evidence_lane_plugin.engine import Empty, Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.registry import WORKFLOWS, ActionContext, ActionSpec
from evidence_lane_plugin.workflow_surface import digest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/evidence-lane-plugin'


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def audit():
    return load(PLUGIN / 'scripts/audit_governed_skills.py', 'skill_audit')


def copy_package(tmp_path):
    target = tmp_path / 'plugin'
    for directory in ['skills', 'schemas/skills', 'sdk/workflows', 'assets']:
        shutil.copytree(PLUGIN / directory, target / directory)
    return target


def test_skills_and_sdk_references_match_actual_registry(tmp_path):
    engine = Engine(tmp_path / 'runtime')
    report = audit().audit(registry=engine.registry, active_surface=True)
    assert report['status'] == 'PASS', report
    assert report['skill_count'] == len(WORKFLOWS)
    assert not report['native_prompt_verified'] and not report['installation_verified']
    catalog = engine.registry.execute('workflow_catalog', {}, ActionContext('client', None, frozenset({'read'})))
    assignments = [a['name'] for w in catalog['workflows'] for a in w['actions']]
    assert Counter(assignments) == Counter(a['name'] for a in engine.registry.schemas())
    assert not catalog['permission_granted']
    for workflow in catalog['workflows']:
        assert workflow['availability'] == ('registered_actions' if workflow['actions'] else 'no_registered_actions')
    strict = audit().audit(registry=engine.registry)
    if strict['retired_skill_directories'] or strict['unreferenced_retained_skill_files']:
        assert strict['status'] == 'FAIL'


def test_dynamic_operation_is_discovered_and_stale_reference_is_detected(tmp_path):
    engine = Engine(tmp_path / 'runtime')
    engine.registry.register(ActionSpec('document_fixture', 'Fixture document operation.', Empty, Empty,
        lambda *_: {}, workflow='execute-project-plan', permission='write', mutates=True, requires_delta=True))
    result = engine.registry.workflow_schemas('execute-project-plan')
    assert any(a['name'] == 'document_fixture' for a in result[0]['actions'])
    report = audit().audit(registry=engine.registry)
    assert any(i['skill'] == 'execute-project-plan' and i['code'] == 'live-action-reference-mismatch' for i in report['issues'])
    with pytest.raises(LaneError) as error:
        engine.registry.execute('document_fixture', {}, ActionContext('reader', 'project', frozenset({'read'})))
    assert error.value.code == 'PERMISSION_DENIED'


@pytest.mark.parametrize('workflow,code', [('invented','UNKNOWN_WORKFLOW'), ('query','UNKNOWN_WORKFLOW'), ('work','UNKNOWN_WORKFLOW')])
def test_unrecognized_or_retired_workflow_is_rejected(tmp_path, workflow, code):
    engine = Engine(tmp_path)
    with pytest.raises(LaneError) as error:
        engine.registry.register(ActionSpec('unsafe_route', 'Cannot become informational.', Empty, Empty,
            lambda *_: {}, workflow=workflow, permission='write', mutates=True))
    assert error.value.code == code


def test_standalone_copy_keeps_local_references_and_detects_missing_resource(tmp_path):
    target = copy_package(tmp_path)
    assert audit().audit(target, active_surface=True)['status'] == 'PASS'
    lifecycle_skill = next(row.skill for row in WORKFLOWS if row.name == 'run-project-lifecycle')
    (target / 'skills' / lifecycle_skill / 'references/shared-boundaries.md').unlink()
    result = audit().audit(target, active_surface=True)
    assert result['status'] == 'FAIL'
    assert any(x['code'] == 'standalone-reference-unavailable' for x in result['issues'])
    assert any(x['code'] == 'package-member-integrity' for x in result['issues'])


@pytest.mark.parametrize('damage,code', [
    ('missing', 'standalone-reference-unavailable'),
    ('tampered', 'package-member-integrity'),
    ('unlinked', 'unreachable-procedure-member'),
])
def test_conditional_procedure_must_be_present_sealed_and_reachable(tmp_path, damage, code):
    target = copy_package(tmp_path)
    path = target / 'skills/manage-project-sources/references/code.md'
    if damage == 'missing':
        path.unlink()
    elif damage == 'tampered':
        path.write_bytes(path.read_bytes() + b'\nChanged procedure.\n')
    else:
        # A self-consistent manifest cannot make an undiscoverable procedure
        # usable. Seal an orphan resource and update both catalog mirrors.
        path = path.with_name('orphan.md')
        path.write_text('# Unlinked procedure\n', encoding='utf-8')
        surface_path = target / 'skills/skill-surface-registry.v4.json'
        surface = json.loads(surface_path.read_bytes())
        record = next(row for row in surface['skills'] if row['name'] == 'manage-project-sources')
        record['members'].append({'path': path.relative_to(target).as_posix(),
                                  'bytes': path.stat().st_size,
                                  'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
        record['members'].sort(key=lambda row: row['path'])
        surface['digest'] = digest({key: value for key, value in surface.items() if key != 'digest'})
        surface_path.write_text(json.dumps(surface), encoding='utf-8')
        (target / 'schemas/skills/skill-registry.v4.json').write_bytes(surface_path.read_bytes())
        sdk_path = target / 'sdk/workflows/skill-workflow-registry.v4.json'
        sdk = json.loads(sdk_path.read_bytes())
        sdk['skill_surface_digest'] = surface['digest']
        sdk_path.write_text(json.dumps(sdk), encoding='utf-8')
    result = audit().audit(target, active_surface=True)
    assert result['status'] == 'FAIL'
    assert any(row['skill'] == 'manage-project-sources' and row['code'] == code for row in result['issues'])


def test_metadata_generation_preserves_invocation_policy_and_is_repeatable(tmp_path):
    target = copy_package(tmp_path)
    ui = target / 'skills/open-project-session/agents/openai.yaml'
    ui.write_text(ui.read_text() + 'policy:\n  allow_implicit_invocation: false\n')
    generator = load(ROOT / 'scripts/generate_workflow_skills.py', 'skill_generator')
    engine = Engine(tmp_path / 'runtime')
    first = generator.generate(engine.registry, target)
    original = ui.read_bytes()
    second = generator.generate(engine.registry, target)
    assert first == second and ui.read_bytes() == original
    assert b'allow_implicit_invocation: false' in original
    assert b'value: "evidence-lane"' in original
    assert audit().audit(target, registry=engine.registry, active_surface=True)['status'] == 'PASS'


def test_resealed_skill_catalog_cannot_change_original_workflow_provenance(tmp_path):
    target = copy_package(tmp_path)
    path = target / 'skills/skill-surface-registry.v4.json'
    surface = json.loads(path.read_bytes())
    lifecycle = next(row for row in surface['skills'] if row['workflow'] == 'run-project-lifecycle')
    lifecycle['source_skill'] = 'manage-project-plan'
    surface['digest'] = digest({key: value for key, value in surface.items() if key != 'digest'})
    path.write_text(json.dumps(surface), encoding='utf-8')
    result = audit().audit(target, active_surface=True)
    assert result['status'] == 'FAIL'
    assert any(row['code'] == 'original-skill-provenance-mismatch' for row in result['issues'])


def test_current_mcp_protocol_exposes_workflow_discovery_without_project_access(tmp_path):
    async def exercise(engine):
        params = StdioServerParameters(command=sys.executable,
            args=['-m','evidence_lane_plugin.mcp_adapter','--runtime-root',str(tmp_path), '--host-profile','codex_cli'],
            env={'PYTHONPATH':str(PLUGIN / 'src')})
        async with (stdio_client(params) as (read, write),
                    ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session):
            await session.initialize()
            result = await session.call_tool('workflow_catalog', {'arguments':{'workflow':'recover-project-state'}})
            assert not result.isError
            body = result.structuredContent['result']
            assert len(body['workflows']) == 1 and body['workflows'][0]['skill'] == 'recover-project-state'
            assert not body['permission_granted']
            assert engine.clients.status()[0]['project_ids'] == []
            current = [
                'run-project-lifecycle', 'retrieve-project-evidence',
                'inspect-project-connectors', 'configure-project-connector',
                'revoke-project-connector', 'close-project-session',
            ]
            for workflow_id in current:
                discovered = await session.call_tool('workflow_catalog', {'arguments': {'workflow': workflow_id}})
                assert not discovered.isError
                catalog = discovered.structuredContent['result']
                assert catalog['workflows'][0]['skill'] == workflow_id
                assert [row['name'] for row in catalog['workflows'][0]['actions']] == [
                    row['name'] for row in engine.registry.schemas() if row['workflow'] == workflow_id]
                assert not catalog['permission_granted']
            for retired in ('lifecycle', 'brain-scaling', 'plugin', 'additional-plugin',
                            'drop-additional-plugin', 'exit-boot'):
                rejected = await session.call_tool(
                    'workflow_catalog', {'arguments': {'workflow': retired}}
                )
                assert rejected.isError
            assert engine.clients.status()[0]['project_ids'] == []
            bad = await session.call_tool('workflow_catalog', {'arguments':{'workflow':'evi-formula'}})
            # The MCP library rejects an invalid enum before the engine handler.
            assert bad.isError
            assert engine.clients.status()[0]['project_ids'] == []
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert 'workflow_catalog' in names and not names.intersection({'pv_status','pv_state_travel_resume','formula_engine_run'})
            classified = await session.call_tool('project_work_classify', {'arguments': {
                'request': 'Analyze and plan this source correction.', 'explicit_work_classes': ['AL', 'PL']}})
            assert not classified.isError, classified
            selection = classified.structuredContent['result']['work_classification']['mode_governance']
            assert [row['mode_id'] for row in selection['contracts']] == ['AL', 'PL']
            assert not selection['selection_authorizes_work']
            assert classified.structuredContent['tool_execution']['env_uop']['owner_skill'] == 'classify-project-work'
            assert engine.clients.status()[0]['project_ids'] == []
    with Engine(tmp_path) as engine, LocalEndpoint(engine):
        asyncio.run(exercise(engine))

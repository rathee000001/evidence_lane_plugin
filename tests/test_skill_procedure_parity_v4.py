"""Current workflow ownership and the typed contracts its procedures depend on."""
from __future__ import annotations

import inspect
from pathlib import Path

from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.registry import WORKFLOWS

ROOT = Path(__file__).resolve().parents[1]


def test_current_business_workflows_keep_distinct_executable_owners(tmp_path):
    assert len(WORKFLOWS) == len({row.name for row in WORKFLOWS}) == 24
    assert len({row.skill for row in WORKFLOWS}) == len(WORKFLOWS)
    assert all(not row.skill.startswith('evi-') for row in WORKFLOWS)
    registry = Engine(tmp_path).registry
    actions = registry.schemas()
    assert {row['workflow'] for row in actions} == {row.name for row in WORKFLOWS}
    for definition in WORKFLOWS:
        assert (ROOT / 'plugins/evidence-lane-plugin/skills' / definition.skill / 'SKILL.md').is_file()
        bindings = [row for row in actions if row['workflow'] == definition.name]
        assert bindings
        for binding in bindings:
            spec = registry.get(binding['name'])
            handler = Path(inspect.getsourcefile(spec.handler)).resolve()
            assert handler.is_relative_to(ROOT / 'plugins/evidence-lane-plugin/src/evidence_lane_plugin')
            assert spec.input_model.__module__.startswith('evidence_lane_plugin.')
            assert spec.output_model.__module__.startswith('evidence_lane_plugin.')
            assert not (spec.mutates and spec.studio_read)


def test_documented_control_procedures_bind_exact_typed_preconditions(tmp_path):
    registry = Engine(tmp_path).registry
    required = {
        'plan_refresh': {'title', 'tasks', 'expected_revision', 'expected_document_digest', 'steer_request_id'},
        'steer_submit': {'source_event_id', 'source_cursor', 'expected_revision', 'intent', 'rationale'},
        'delta_enter_planned': {'task_id', 'plan_revision', 'contract_digest'},
        'connector_revoke': {'plugin_id', 'expected_version', 'expected_digest'},
        'memory_checkpoint': {'task_id', 'plan_revision', 'contract_digest', 'locator_ids'},
        'continuation_accept': {'continuation_id', 'continuation_digest'},
        'learning_revoke': {'version_id', 'expected_digest', 'reason'},
    }
    for name, fields in required.items():
        spec = registry.get(name)
        schema = spec.input_model.model_json_schema()
        assert fields <= set(schema['required'])
        assert schema['additionalProperties'] is False
        assert spec.mutates and spec.permission != 'read' and not spec.studio_read
    # Planned entry cannot accept an ad hoc operation or source-route override.
    assert set(registry.get('delta_enter_planned').input_model.model_fields) == required['delta_enter_planned']
    native = registry.get('continuation_offer').input_model.model_fields['require_native_attestation']
    assert native.annotation is bool
    for name in ('plan_read', 'steer_preview', 'toolchain_resolve', 'memory_read', 'continuation_context'):
        spec = registry.get(name)
        assert not spec.mutates and spec.permission == 'read' and not spec.requires_delta

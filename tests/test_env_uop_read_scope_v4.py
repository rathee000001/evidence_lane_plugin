"""Bounded aggregate reads retain live policy, schema and disclosure gates."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from evidence_lane_plugin.flash_authority import SessionFlashAuthority

from .test_code_profile_v4 import call, create_plan, execute
from .test_code_profile_v4 import code_system as code_system  # noqa: PLC0414
from .test_project_search_v4 import bytes_digest


def isolated_policy(engine, tmp_path):
    root = Path(__file__).resolve().parents[1] / 'plugins/evidence-lane-plugin'
    manifest = json.loads((root / 'env/SESSION_FLASH_MANIFEST.json').read_bytes())
    for entry in [*manifest['members'], {'path': 'env/SESSION_FLASH_MANIFEST.json'}]:
        path = tmp_path / 'policy' / entry['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((root / entry['path']).read_bytes())
    authority = SessionFlashAuthority(asset_root=tmp_path / 'policy')
    authority.verify(registry=engine.registry)
    engine.registry.control_plane.flash = authority
    return tmp_path / 'policy'


def test_changed_flash_bytes_fail_before_next_nested_read(code_system, tmp_path, monkeypatch):
    engine, store, _ = code_system
    create_plan(code_system)
    execute(code_system)
    policy = isolated_policy(engine, tmp_path)
    target = policy / 'env/codex-environment-policy.v4.json'
    original = engine.registry.execute
    changed = False

    def changing(name, arguments, context):
        nonlocal changed
        value = original(name, arguments, context)
        if name == 'code_current' and not changed:
            changed = True
            target.write_bytes(target.read_bytes() + b' ')
        return value

    monkeypatch.setattr(engine.registry, 'execute', changing)
    before = bytes_digest(store.root)
    response = call(code_system, 'lane_search', {'lane_id': 'local_code', 'query': 'greeting'})
    assert response.status == 'error' and response.error.code == 'SESSION_FLASH_MEMBER_HASH_MISMATCH'
    assert before == bytes_digest(store.root)


@pytest.mark.parametrize('changed_action,code', [
    ('code_query', 'ENV_UOP_ACTION_BINDING_CHANGED'),
    ('project_status', 'SESSION_FLASH_REGISTRY_CHANGED'),
])
def test_changed_selected_or_unrelated_action_prevents_result_release(code_system, monkeypatch, changed_action, code):
    engine, store, _ = code_system
    create_plan(code_system)
    execute(code_system)
    original = engine.registry.execute
    initial = engine.registry.get(changed_action)
    changed = False

    def changing(name, arguments, context):
        nonlocal changed
        value = original(name, arguments, context)
        if name == 'code_current' and not changed:
            changed = True
            # Fault injection bypasses the public frozen-registry API. The
            # current read must still withhold data when that fault is found.
            engine.registry._actions[changed_action] = replace(initial, description=initial.description + ' altered')
        return value

    monkeypatch.setattr(engine.registry, 'execute', changing)
    before = bytes_digest(store.root)
    try:
        response = call(code_system, 'lane_search', {'lane_id': 'local_code', 'query': 'greeting'})
        assert response.status == 'error' and response.error.code == code
        assert before == bytes_digest(store.root)
    finally:
        engine.registry._actions[changed_action] = initial
    assert engine.registry.control_plane._read_batch.get() is None


def test_scope_does_not_survive_a_failed_read(code_system, monkeypatch):
    engine, store, _ = code_system
    create_plan(code_system)
    execute(code_system)
    original = engine.registry.execute
    from evidence_lane_plugin.errors import LaneError

    def failed(name, arguments, context):
        if name == 'code_current':
            raise LaneError('FIXTURE_OWNER_READ_FAILED', 'Deliberate bounded read failure')
        return original(name, arguments, context)

    before = bytes_digest(store.root)
    with monkeypatch.context() as patch:
        patch.setattr(engine.registry, 'execute', failed)
        response = call(code_system, 'lane_search', {'lane_id': 'local_code', 'query': 'greeting'})
        assert response.error.code == 'FIXTURE_OWNER_READ_FAILED'
    assert engine.registry.control_plane._read_batch.get() is None
    fresh = call(code_system, 'lane_search', {'lane_id': 'local_code', 'query': 'greeting'})
    assert fresh.status == 'ok' and fresh.result['arms'][0]['state'] == 'hit'
    assert before == bytes_digest(store.root)

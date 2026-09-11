"""Adapted v3 grant/ambiguity regressions plus the new project-scoped boundary."""

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from evidence_lane_plugin.connector_governance import (
    ROUTE_GUARD_ORDER,
    ConnectorGovernance,
    PluginRegistration,
)
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.projects import ProjectAccess
from evidence_lane_plugin.registry import ActionContext
from evidence_lane_plugin.storage import ProjectStore
from evidence_lane_plugin.writers import WriterLease
from pydantic import ValidationError


@pytest.fixture
def state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "allowed").mkdir()
    store = ProjectStore.create(tmp_path / "state", source)
    access = ProjectAccess(store)
    access.initialize()
    permissions = frozenset({"admin", "read", "write", "tools"})
    grant = access.issue("client", permissions, [source])
    context = ActionContext("client", store.project_id, permissions)
    governance = ConnectorGovernance(store, lanes={"code", "research"},
                                     hosts={"codex_cli", "codex_desktop"}, actions={"source_read", "source_write"})
    with WriterLease(store, "engine") as lease:
        governance.initialize(lease)
        yield governance, context, lease, access, grant


def registration(governance, **changes):
    return PluginRegistration.model_validate({
        "plugin_id": "alpha-research", "name": "Research", "plugin_kind": "connector",
        "description": "Reads a selected source.", "purpose": "Read one project source.",
        "config_env_keys": ["SOURCE_API_TOKEN"], "capabilities": ["source_read"],
        "allowed_lanes": ["research"], "allowed_actions": ["source_read", "source_write"],
        "write_roots": [str(governance.store.source_root / "allowed")],
        "expires_at": "NO_EXPIRY", "role": "source_reader", "role_schema": {"source_hash": "blob_hash"},
        "host_profiles": ["codex_cli"], "backend_runtime": "external_mcp",
    } | changes)


def route(governance, **changes):
    return governance.route(**{"capability": "source_read", "action": "source_read",
                               "lane": "research", "host": "codex_cli"} | changes)


def authorize(governance, context, lease, **changes):
    return governance.authorize_execution(**{
        "context": context, "lease": lease, "plugin_id": "alpha-research", "version": 1,
        "capability": "source_read", "action": "source_read", "lane": "research",
        "host": "codex_cli", "permission": "read", "runtime_ready": True,
    } | changes)


def test_persistent_plugin_scope_and_ordered_ambiguity_guards(state):
    governance, context, lease, _, _ = state
    for identifier in ("alpha-research", "beta-research"):
        governance.configure(registration(governance, plugin_id=identifier), context, lease)
    before = governance.store.database.read_bytes()
    result = route(governance)
    assert result["decision"] == "ambiguous"
    assert result["selected"] is None
    assert result["guard_order"] == list(ROUTE_GUARD_ORDER)
    assert all(item["eligible"] for item in result["guard_trace"])
    assert route(governance, preferred="alpha-research")["selected"]["purpose"] == "Read one project source."
    assert route(governance, preferred="absent-tool")["decision"] == "requested_plugin_denied"
    assert governance.store.database.read_bytes() == before


def test_configuration_preserves_versions_and_rejects_stale_write(state):
    governance, context, lease, _, _ = state
    first = registration(governance)
    governance.configure(first, context, lease)
    with pytest.raises(LaneError, match="current version"):
        governance.configure(first, context, lease)
    assert not governance.configure(first, context, lease, expected_version=1)["changed"]
    changed = registration(governance, purpose="Read a revised selection.")
    assert governance.configure(changed, context, lease, expected_version=1)["version"] == 2
    with pytest.raises(LaneError) as error:
        authorize(governance, context, lease)
    assert error.value.code == "PLUGIN_ROUTE_DENIED"
    with governance.store.connection(read_only=True) as connection:
        assert connection.execute("SELECT count(*) FROM extensions_versions").fetchone()[0] == 2
        events = connection.execute("SELECT * FROM extensions_events ORDER BY sequence").fetchall()
        assert events[1]["prior_digest"] == events[0]["digest"]
        assert "SOURCE_API_TOKEN" not in events[0]["details_json"]


def test_revocation_survives_reopen_and_prevents_reactivation(state):
    governance, context, lease, _, _ = state
    value = registration(governance)
    governance.configure(value, context, lease)
    governance.revoke(value.plugin_id, context, lease)
    reopened = ConnectorGovernance(ProjectStore(governance.project.root, read_only=True),
                                   lanes={"research"}, hosts={"codex_cli"}, actions={"source_read"})
    assert route(reopened)["decision"] == "unavailable"
    with pytest.raises(LaneError) as error:
        governance.configure(value, context, lease, expected_version=1)
    assert error.value.code == "PLUGIN_REVOKED"


def test_revoked_history_cannot_hide_a_second_live_candidate(state):
    from evidence_lane_plugin.storage import json_text

    governance, context, lease, _, _ = state
    for identity in ('aaa-live', 'zzz-live'):
        governance.configure(registration(governance, plugin_id=identity), context, lease)
    # Historical migration fixture: archived identities precede the second
    # current candidate and exceed the old unfiltered page of 4096 rows.
    with lease.transaction('receipts') as connection:
        for index in range(4096):
            identity = f'archived-{index:04}'
            value = registration(governance, plugin_id=identity).model_dump()
            raw = json_text(value)
            connection.execute('INSERT INTO extensions_registration VALUES(?,?,?)', (identity, 1, '2026-01-01T00:00:00+00:00'))
            connection.execute('INSERT INTO extensions_versions VALUES(?,?,?,?,?,?)',
                (identity, 1, raw, hashlib.sha256(raw.encode()).hexdigest(), '2026-01-01T00:00:00+00:00', context.client_id))
    before = governance.project.pv_head()
    result = route(governance)
    assert result['decision'] == 'ambiguous' and result['selected'] is None
    assert [row['plugin_id'] for row in governance.active_catalog()] == ['aaa-live', 'zzz-live']
    assert governance.project.pv_head() == before


@pytest.mark.parametrize('change', ['identity', 'expiry', 'schema', 'missing_version'])
def test_saved_registration_is_bound_to_its_indexed_identity_and_schema(state, change):
    from evidence_lane_plugin.storage import json_text

    governance, context, lease, _, _ = state
    governance.configure(registration(governance), context, lease)
    with lease.transaction('receipts') as connection:
        row = connection.execute('SELECT registration_json FROM extensions_versions').fetchone()
        value = json.loads(row[0])
        if change == 'missing_version':
            connection.execute('UPDATE extensions_registration SET current_version=42')
        else:
            if change == 'identity':
                value['plugin_id'] = 'different-reader'
            elif change == 'expiry':
                value['expires_at'] = '2030-01-01T00:00:00'
            else:
                value['role_schema'] = {'source_hash': 'unregistered_type'}
            raw = json_text(value)
            connection.execute('UPDATE extensions_versions SET registration_json=?,digest=?',
                (raw, hashlib.sha256(raw.encode()).hexdigest()))
    with pytest.raises(LaneError) as error:
        route(governance)
    assert error.value.code == 'PLUGIN_RECORD_INTEGRITY'


def test_registration_page_rejects_excess_bytes_before_materializing_payloads(state):
    governance, context, lease, _, _ = state
    governance.configure(registration(governance, description='x' * 1000, purpose='y' * 1000), context, lease)
    with pytest.raises(LaneError) as error:
        governance.catalog(max_bytes=1024)
    assert error.value.code == 'PLUGIN_READ_BUDGET'


def test_project_access_is_rechecked_after_client_grant_revocation(state):
    governance, context, lease, access, grant = state
    governance.configure(registration(governance), context, lease)
    access.revoke(grant, writer=lease)
    for call in (lambda: authorize(governance, context, lease),
                 lambda: governance.revoke("alpha-research", context, lease)):
        with pytest.raises(LaneError) as error:
            call()
        assert error.value.code == "PERMISSION_DENIED"


def test_expiry_host_lane_action_and_runtime_fail_closed(state):
    governance, context, lease, _, _ = state
    current = datetime.now(UTC)
    governance.clock = lambda: current
    governance.configure(registration(governance, expires_at=(current + timedelta(seconds=10)).isoformat()), context, lease)
    assert route(governance)["backend_execution_authorized"] is False
    for values in ({"host": "codex_desktop"}, {"lane": "code"}, {"action": "unknown"}):
        assert route(governance, **values)["decision"] == "unavailable"
    with pytest.raises(LaneError) as error:
        authorize(governance, context, lease, runtime_ready=False)
    assert error.value.code == "PLUGIN_RUNTIME_UNAVAILABLE"
    governance.clock = lambda: current + timedelta(seconds=10)
    assert route(governance)["decision"] == "unavailable"


def test_write_target_must_match_both_project_and_extension_grants(state):
    governance, context, lease, _, _ = state
    governance.configure(registration(governance), context, lease)
    authorize(governance, context, lease, permission="write", path=governance.store.source_root / "allowed/output.md")
    with pytest.raises(LaneError) as error:
        authorize(governance, context, lease, permission="write", path=governance.store.source_root / "outside.md")
    assert error.value.code == "PLUGIN_WRITE_SCOPE_DENIED"
    with pytest.raises(LaneError) as error:
        authorize(governance, context, lease, permission="write", path=governance.store.source_root.parent / "escape.md")
    assert error.value.code == "PERMISSION_DENIED"


@pytest.mark.parametrize("changes", [
    {"role_schema": {"api_key": "text"}}, {"role_schema": {"count": "sql"}},
    {"config_env_keys": ["TOKEN=value"]}, {"expires_at": "2026-10-01"}, {"purpose": " "},
])
def test_invalid_role_and_secret_configuration_rejected_before_storage(state, changes):
    governance, _, _, _, _ = state
    with pytest.raises(ValidationError):
        registration(governance, **changes)
    assert governance.catalog() == []


def test_output_schema_checks_strict_types_and_hides_bad_values(state):
    governance, _, _, _, _ = state
    plugin = {"role_schema": {"count": "integer", "observed_at": "datetime"}}
    assert len(governance.validate_role_output(plugin, {"count": 1, "observed_at": "2026-09-05T00:00:00Z"})) == 64
    for value in ({"count": True, "observed_at": "2026-09-05T00:00:00Z"},
                  {"count": 1, "observed_at": "private-value-must-not-appear"}):
        with pytest.raises(LaneError) as error:
            governance.validate_role_output(plugin, value)
        assert "private-value" not in str(error.value)


def test_project_identity_and_writer_must_both_match(state, tmp_path):
    governance, context, lease, _, _ = state
    source = tmp_path / "second-source"
    source.mkdir()
    second = ProjectStore.create(tmp_path / "second-state", source)
    with WriterLease(second, "engine") as other:
        with pytest.raises(LaneError) as error:
            governance.configure(registration(governance), context, other)
        assert error.value.code == "PROJECT_BINDING_MISMATCH"
    forged_context = ActionContext(context.client_id, second.project_id, context.permissions)
    with pytest.raises(LaneError) as error:
        governance.configure(registration(governance), forged_context, lease)
    assert error.value.code == "PROJECT_BINDING_MISMATCH"

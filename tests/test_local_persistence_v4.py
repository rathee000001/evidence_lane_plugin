from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.sdk import ActionRequest

from tests.storage_fixtures_v4 import declare_local_storage


def status(engine, project_id):
    _, session = engine.clients.connect(
        ConnectRequest(
            hello={"configured_profile": "codex_desktop_stable"},
            projects=[ProjectSelection(project_id=project_id)],
        )
    )
    return PublicActionSDKDispatcher(engine).execute(
        ActionRequest(action="storage_status", project_id=project_id, arguments={}),
        session,
    )


def test_project_storage_is_local_persistence_status_without_connector_actions(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    state = tmp_path / "state"
    runtime = tmp_path / "runtime"
    with Engine(runtime) as engine:
        registered = engine.directory.register(state, source_root=source, create=True, read_only=False)
        result = status(engine, registered["project_id"])
        assert result.status == "ok", result.error
        persistence = result.result["storage_persistence"]
        assert persistence["policy_satisfied"] is False
        assert persistence["availability_reason"] == "persistent_storage_not_declared"
        assert persistence["selection_recorded"] is False
        names = {row["name"] for row in engine.registry.schemas()}
        assert {"project_register", "storage_status"} <= names
        assert not {"storage_connector_inspect", "storage_connector_select"} & names

    declare_local_storage(runtime, state)
    with Engine(runtime) as engine:
        result = status(engine, registered["project_id"])
        assert result.status == "ok", result.error
        persistence = result.result["storage_persistence"]
        assert persistence["policy_satisfied"] is True
        assert persistence["availability_reason"] == "persistent_local_operator_declaration"
        assert persistence["backend"]["connection"] == "local_api"

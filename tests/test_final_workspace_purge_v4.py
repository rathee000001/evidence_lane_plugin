from scripts.audit_final_workspace_purge import audit


def test_final_workspace_has_one_current_owner_for_every_executable_surface():
    result = audit()
    assert result["status"] == "PASS", result["violations"]
    assert result["counts"] == {
        "actions": 297,
        "skills": 24,
        "authorities": 9,
        "sectors": 13,
        "tools": 103,
        "license_records": 103,
    }
    assert result["project_data_changed"] is False
    assert result["installed_execution_claimed"] is False

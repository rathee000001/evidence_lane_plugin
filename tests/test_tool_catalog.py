from evidence_lane_plugin import tool_catalog


def test_complete_retained_v4_inventory_excludes_final_purge_rows():
    catalog = tool_catalog.snapshot({"tools": []})
    assert catalog["schema_version"] == 4
    assert catalog["base_entry_count"] == 103
    assert catalog["additional_entry_count"] == 0
    assert catalog["counts"] == {"retained": 103}
    items = {item["tool_id"]: item for item in catalog["entries"]}
    assert items["FastMCP"]["kind"] == "mcp_framework"
    assert items["Git"]["kind"] == "native_tool"
    assert not {
        "Package_sealer", "OpenJDK", "Jackcess", "OneNote_Parser",
        "RapidFuzz", "Promptfoo", "TruLens", "DeepEval", "Helicone",
        "Docker", "Kubernetes", "AWS_Lambda", "Google_Cloud_Run",
        "AWS", "Azure", "Google_Cloud", "Vercel_Git_integration",
        "GitHub_Actions", "GitHub_MCP_Server", "Filesystem_MCP_Server",
        "PostgreSQL_MCP_Server", "Slack_MCP_Server",
        "psutil",
    }.intersection(items)
    # Direct current Codex ENV/UOP classification remains an internal component.
    assert items['ENV_UOP_classifier']['lifecycle'] == 'retained'
    assert all(not item["execution_authorized_by_catalog"] for item in items.values())
    assert all(
        not set(item["lanes"]) & {"mode", "discussion", "analysis", "brain_loader"}
        for item in items.values()
    )


def test_observations_do_not_promote_presence_to_execution_or_connection():
    catalog = tool_catalog.snapshot(
        {
            "tools": [
                {"name": "mcp", "state": "present", "execution_state": "not_executed"},
                {"name": "sqlite_fts5", "state": "verified", "execution_state": "passed"},
            ]
        }
    )
    items = {item["tool_id"]: item for item in catalog["entries"]}
    assert items["MCP_Python_SDK"]["readiness"] == "present"
    assert items["MCP_Python_SDK"]["execution_state"] == "not_executed"
    assert items["SQLite_FTS5_BM25"]["execution_state"] == "passed"
    assert items["Pinecone"]["readiness"] == "connection_unverified"
    assert items["Pinecone"]["configuration_state"] == "not_assessed"
    assert items["Pinecone"]["execution_state"] == "not_verified"
    assert items["SQLite_FTS5_BM25"]["v4_operation_qualification"] == "pending"


def test_catalog_snapshots_are_isolated_and_preserve_lane_order():
    first = tool_catalog.snapshot({"tools": []})
    git = next(item for item in first["entries"] if item["tool_id"] == "Git")
    assert "local_code" in git["lanes"] and "github_code" in git["lanes"]
    assert git["primary"] == ["CODE"] and git["action_order"]["CODE"] == 1
    first["entries"].clear()
    assert len(tool_catalog.snapshot({"tools": []})["entries"]) == 103

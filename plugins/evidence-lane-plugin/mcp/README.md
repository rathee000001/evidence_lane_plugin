# Evidence Lane MCP

The packaged launch command is `scripts/run_mcp.py`. The Python binding in `evidence_lane_mcp.py` exports the same `mcp_server.run_server` and requires an explicit local runtime or remote configuration. Both connect through the thin stdio adapter to the authenticated persistent engine.

`mcp-manifest.v4.json` identifies the current action bindings and their hashes. Actions, schemas and tool annotations derive from the engine registry. Legacy v1 metadata awaiting the final source purge does not register tools or authorize execution.

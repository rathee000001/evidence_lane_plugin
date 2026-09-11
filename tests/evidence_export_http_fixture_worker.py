"""Run the production evidence-export worker through controlled HTTPX."""

import json
import runpy
import sys
from pathlib import Path

import httpx

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / "plugins/evidence-lane-plugin/src"))

from tests.evidence_export_http_fixture import ExportTransport

fixture = json.loads((Path.cwd() / "fixture.json").read_bytes())
original = httpx.Client
transport = ExportTransport(
    fixture["tool_id"],
    fixture["kind"],
    fixture.get("signal"),
    fixture.get("configuration"),
)
httpx.Client = lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs)
runpy.run_module("evidence_lane_plugin._evidence_export_worker", run_name="__main__")

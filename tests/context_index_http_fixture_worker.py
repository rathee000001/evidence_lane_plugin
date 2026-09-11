"""Run the production context-index worker through a controlled HTTPX transport."""
import json
import runpy
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'plugins/evidence-lane-plugin/src'))
import httpx

from tests.context_index_http_fixture import IndexTransport

fixture = json.loads((Path.cwd() / 'fixture.json').read_bytes())
original = httpx.Client
transport = IndexTransport(fixture['tool_id'], fixture['operation'], fixture.get('configuration'))
httpx.Client = lambda **kwargs: original(transport=httpx.MockTransport(transport), **kwargs)
runpy.run_module('evidence_lane_plugin._context_index_worker', run_name='__main__')

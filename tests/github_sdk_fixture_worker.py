"""Exercise the production worker with deterministic SDK transport fixtures."""
import json
import runpy
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'plugins/evidence-lane-plugin/src'))
import requests

from tests.github_sdk_fixture import GitHubFixture

configuration = json.loads((Path.cwd() / 'fixture.json').read_bytes())
fixture = GitHubFixture(configuration)
requests.Session.get = lambda session, url, **kwargs: fixture.get(session, url, **kwargs)
runpy.run_module('evidence_lane_plugin._github_inspection_worker', run_name='__main__')

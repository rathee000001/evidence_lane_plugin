"""Generate package hook bindings from the implemented engine event contract."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/evidence-lane-plugin'
sys.path.insert(0, str(PLUGIN / 'src'))
from evidence_lane_plugin.hook_contract import hook_manifest, hook_registry

for name, value in [('hooks.json', hook_manifest()), ('hook-event-registry.v4.json', hook_registry())]:
    (PLUGIN / 'hooks' / name).write_text(
        json.dumps(value, indent=2) + '\n', encoding='utf8', newline='\n'
    )
print(json.dumps({'events': len(hook_registry()['events']), 'generated': ['hooks/hooks.json', 'hooks/hook-event-registry.v4.json']}))

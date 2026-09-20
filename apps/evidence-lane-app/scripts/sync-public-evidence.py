"""Read-only source inspection; writes the website's public contract snapshot only."""
import json, hashlib
from pathlib import Path
APP=Path(__file__).resolve().parents[1]
ROOT=APP.parents[1]
PLUGIN=ROOT/'plugins/evidence-lane-plugin'
inputs=['skills/skill-surface-registry.v4.json','sdk/workflows/skill-workflow-registry.v4.json','.codex-plugin/plugin.json','studio/installation.contract.v4.json','provisioning/release-binding.v4.json','env/orchestration.policy.v4.json','LICENSE.md']
source_bytes={path:(PLUGIN/path).read_bytes() for path in inputs}
def read(path):return json.loads(source_bytes[path])
registry=read(inputs[0]); workflows=read(inputs[1]); release=read(inputs[4])
snapshot={'version':read(inputs[2])['version'],'installationVerified':False,'releaseStatus':'Source candidate; public availability and installed-native execution not verified by this website','sourceHashes':{p:hashlib.sha256(source_bytes[p]).hexdigest() for p in inputs},'skills':[{'name':s['name'],'description':s['description'],'actions':s['actions']} for s in registry['skills']],'actions':{a['name']:a for w in workflows['workflows'] for a in w['actions']},'release':{k:release[k] for k in ['release_ref','assets_sha256','bundle_plan_sha256','source_manifest_sha256']},'assets':[{k:a[k] for k in ['filename','bytes','sha256','condition']} for a in release['assets']]}
assert len(snapshot['skills'])==24
assert all((PLUGIN/path).read_bytes()==content for path,content in source_bytes.items()), 'Source changed during website snapshot; retry a coherent read.'
out=APP/'app/data/backend.json';out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(snapshot,indent=2)+'\n',encoding='utf-8')
print('Website contract snapshot: 24 skills; %s actions; no runtime readiness inferred.'%len(snapshot['actions']))

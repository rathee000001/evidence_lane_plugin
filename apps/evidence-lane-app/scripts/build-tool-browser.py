"""Read current backend catalogues into a website-only, non-operational projection."""
from pathlib import Path
import json,hashlib,os
app=Path(__file__).resolve().parents[1];base=app.parents[1]/'plugins/evidence-lane-plugin'
paths=['toolchains/shared-toolchain.v4.json','toolchains/operation-toolchains.v4.json','sdk/internal/public-action-registry.v4.json','.codex-plugin/plugin.json']
source_bytes={p:(base/p).read_bytes() for p in paths}
catalog,operation_map,registry,plugin_manifest=[json.loads(source_bytes[p]) for p in paths]
descriptions={a['name']:a for a in registry['actions']}
operations=[]
for op in operation_map['operations']:
 a=descriptions[op['operation']]
 operations.append({'id':op['operation'],'description':a['description'],'profile':a['profile'],'workflow':a['workflow'],'mutates':a['mutates'],'permission':a['permission'],'requiresTask':a['requires_delta'],'queued':a['queued'],'tools':sorted({t for r in op['routes'] for t in r['tool_ids']}),'workers':op['worker_operations'],'routes':[{'id':r['route_id'],'tools':r['tool_ids'],'provider':r['provider'],'reason':r['reason'],'fidelity':r['fidelity']} for r in op['routes']]})
tools=[]
for entry in catalog['entries']:
 used=[o['id'] for o in operations if entry['tool_id'] in o['tools']]
 tools.append({'id':entry['tool_id'],'name':entry['tool_id'].replace('_',' '),'kind':entry['kind'],'provisioning':entry['provisioning_route'].replace('_',' '),'packages':[p if isinstance(p,str) else json.dumps(p) for p in entry['packages']],'required':entry['installation_required_for_windows_bundle'],'external':entry['external_configuration_required'],'license':entry['license_grant_required'],'installation':entry['installation_state'],'execution':entry['adapter_execution_state'],'operations':used})
workers=[{'id':w,'operations':[o['id'] for o in operations if w in o['workers']]} for w in sorted({w for o in operations for w in o['workers']})]
payload={'toolCount':len(tools),'actionCount':len(operations),'workerOperationCount':len(workers),'tools':tools,'operations':operations,'workers':workers,'sourceCandidate':plugin_manifest['version'],'nativeInstallationVerified':False,'installedExecutionClaimed':False,'references':[{'path':p,'sha256':hashlib.sha256(source_bytes[p]).hexdigest()} for p in paths]}
assert payload['toolCount']==catalog['retained_tool_count'] and payload['actionCount']==registry['action_count']
assert all((base/p).read_bytes()==source_bytes[p] for p in paths), 'Backend sources changed during projection; retry from a coherent read.'
output=app/'app/data/tool-browser.json';temporary=output.with_suffix('.json.tmp')
temporary.write_text(json.dumps(payload,indent=2),encoding='utf-8');os.replace(temporary,output)
print(json.dumps({k:payload[k] for k in ['toolCount','actionCount','workerOperationCount']}))

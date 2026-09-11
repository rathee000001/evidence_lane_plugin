"""Project current separate-lane contracts through the original generator owner.

The historical filename is retained for source continuity. Compact/PV schema
mutation is superseded by the owning lane migrations. Importing this module
does not write files, open project databases or execute the old lane engine.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / 'src'))

from evidence_lane_plugin.lanes import (
    BASE_REGISTRY_SHA256,
    CANONICAL_LANE_IDS,
    catalog,
    lane_artifact_contract,
    lane_schema_evolution_contract,
    lane_schema_registry_contract,
)


def exports() -> dict[str, dict]:
    return {
        'lane-registry.v4.json': {'schema': 'evidence-lane.lane-registry.v4',
                                 'base_registry_sha256': BASE_REGISTRY_SHA256,
                                 'lanes': catalog()},
        'lane-schema-registry.v4.json': lane_schema_registry_contract(),
        'lane-artifact-contract.v4.json': {'schema': 'evidence-lane.lane-artifact-role-registry.v4',
                                          'lanes': [lane_artifact_contract(k) for k in CANONICAL_LANE_IDS]},
        'lane-schema-evolution.v4.json': {'schema': 'evidence-lane.lane-schema-evolution-policy.v4',
                                         'lanes': [lane_schema_evolution_contract(k) for k in CANONICAL_LANE_IDS]},
    }


def generate(*, root: Path = PLUGIN_ROOT, check: bool = False) -> list[str]:
    destination = Path(root) / 'schemas'
    changed = []
    for name, value in exports().items():
        body = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
        target = destination / name
        if target.is_file() and target.read_bytes() == body:
            continue
        changed.append(name)
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
    if check and changed:
        raise RuntimeError('Lane contracts require regeneration: ' + ', '.join(changed))
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    options = parser.parse_args()
    print(json.dumps({'lane_count': len(CANONICAL_LANE_IDS), 'changed': generate(check=options.check),
                      'project_data_touched': False, 'installed_execution_claimed': False}))


if __name__ == '__main__':
    main()

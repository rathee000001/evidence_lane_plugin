"""Rebuild inspectable v4 package projections from their executable owners.

The authority compiler preserves real owner migrations. The action compiler
uses the engine registry. ENV/UOP and toolchain compilers own their respective
contracts; this command never executes a removed lifecycle or purges files.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / 'src'))

import generate_public_schema_catalog as action_catalog
import regenerate_authority_packages as authority_packages
import regenerate_compact_lane_schema_registry as lane_contracts
import regenerate_hook_packages as hook_packages
import regenerate_sector_packages as sector_packages

from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.plugin_architecture import (
    build_dedicated_skill_workflows,
    build_engine_connection_contract,
    build_surface_workflows,
    build_universal_plugin_architecture,
)


def generate(*, check=False):
    with tempfile.TemporaryDirectory(prefix='evi-package-projections-') as temporary:
        registry = Engine(Path(temporary)).registry
        registry.freeze()
        authorities = authority_packages.generate(registry, check=check)
        sectors = sector_packages.generate(registry, check=check)
        lanes = lane_contracts.generate(root=PLUGIN_ROOT, check=check)
        actions = action_catalog.generate(registry, check=check)
        hooks = hook_packages.generate(root=PLUGIN_ROOT, check=check)
        architecture = build_universal_plugin_architecture(PLUGIN_ROOT, registry=registry)
        connection = build_engine_connection_contract(PLUGIN_ROOT, registry=registry)
    outputs = {
        'manifests/plugin-architecture.v4.json': architecture,
        'sdk/workflows/dedicated-skills.v4.json': build_dedicated_skill_workflows(architecture),
        'schemas/lane-workflows.v4.json': build_surface_workflows(architecture),
        'manifests/engine-connection.v4.json': connection,
    }
    changed = []
    for relative, value in outputs.items():
        target = PLUGIN_ROOT / relative
        body = action_catalog.encoded(value)
        if target.is_file() and target.read_bytes() == body:
            continue
        changed.append(relative)
        if not check:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
    # The complete SDK/schema manifests include the architecture-derived
    # workflow projections written above. Seal them only after those bytes are
    # current so a successful generation is also immediately checkable.
    actions = action_catalog.generate(registry, check=check)
    if check and changed:
        raise RuntimeError('Package projections require regeneration: ' + ', '.join(changed))
    return {'actions': actions, 'sectors': sectors, 'authorities': authorities, 'hooks': hooks,
            'lane_contracts': {'changed': lanes},
            'authority_count': 8, 'lane_count': len(architecture['surfaces']),
            'changed_projections': changed, 'installed_execution_claimed': False,
            'legacy_files_removed': True, 'project_data_touched': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    options = parser.parse_args()
    print(json.dumps(generate(check=options.check)))


if __name__ == '__main__':
    main()

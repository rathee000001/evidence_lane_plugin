"""Rebuild inspectable v4 package projections from their executable owners.

The authority compiler preserves real owner migrations. The action compiler
uses the engine registry. ENV/UOP and toolchain compilers own their respective
contracts. It removes only the explicitly enumerated generated aliases whose
current replacements are already produced by this same generator.
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
import generate_studio_linkage as studio_linkage
import generate_toolchain_execution_matrix as toolchain_execution_matrix
import generate_toolchain_license_records as license_records
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

RETIRED_GENERATED_ALIASES = (
    "sdk/delta/lifecycle.workflow.v4.json",
    "sdk/delta/state-travel.workflow.v4.json",
    "sdk/host/state-travel.contract.v4.json",
)


def retire_generated_aliases(*, check: bool) -> list[str]:
    present = [relative for relative in RETIRED_GENERATED_ALIASES if (PLUGIN_ROOT / relative).exists()]
    if check and present:
        raise RuntimeError("Retired generated aliases remain: " + ", ".join(present))
    if not check:
        for relative in present:
            (PLUGIN_ROOT / relative).unlink()
    return present


def generate(*, check=False):
    retired = retire_generated_aliases(check=check)
    with tempfile.TemporaryDirectory(prefix='evidence-lane-package-projections-') as temporary:
        registry = Engine(Path(temporary)).registry
        registry.freeze()
        lanes = lane_contracts.generate(root=PLUGIN_ROOT, check=check)
        authorities = authority_packages.generate(registry, check=check)
        sectors = sector_packages.generate(registry, check=check)
        actions = action_catalog.generate(registry, check=check)
        hooks = hook_packages.generate(root=PLUGIN_ROOT, check=check)
        licenses = license_records.generate(check=check)
        execution_matrix = toolchain_execution_matrix.generate(check=check)
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
    studio = studio_linkage.generate(check=check)
    if check and changed:
        raise RuntimeError('Package projections require regeneration: ' + ', '.join(changed))
    return {'actions': actions, 'sectors': sectors, 'authorities': authorities, 'hooks': hooks,
            'licenses': licenses, 'studio_linkage': studio,
            'toolchain_execution_matrix': execution_matrix,
            'lane_contracts': {'changed': lanes},
            'authority_count': authorities['authority_count'],
            'lane_count': len(architecture['surfaces']),
            'changed_projections': changed, 'installed_execution_claimed': False,
            'retired_generated_aliases': retired,
            'legacy_files_removed': not any((PLUGIN_ROOT / relative).exists() for relative in RETIRED_GENERATED_ALIASES),
            'project_data_touched': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    options = parser.parse_args()
    print(json.dumps(generate(check=options.check)))


if __name__ == '__main__':
    main()

"""Audit first-class skill packages against their actual engine registry.

The default is the strict final-package gate. --active-surface checks only the
declared retained files while the user's final-purge step remains pending.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SOURCE = PLUGIN_ROOT / 'src'
sys.path.insert(0, str(SOURCE))
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.registry import WORKFLOWS
from evidence_lane_plugin.storage import reject_links
from evidence_lane_plugin.workflow_surface import digest, skill_action_reference


def _members(folder, plugin):
    result = []
    for path in sorted(folder.rglob('*')):
        reject_links(path, plugin)
        if path.is_file():
            result.append({'path':path.relative_to(plugin).as_posix(), 'bytes':path.stat().st_size,
                           'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    return result


def audit(plugin_root=PLUGIN_ROOT, *, registry=None, active_surface=False):
    plugin_root = plugin_root.resolve()
    if registry is None:
        with tempfile.TemporaryDirectory(prefix='evi-skill-audit-') as temporary:
            registry = Engine(Path(temporary)).registry
    issues = []
    def issue(skill, code):
        issues.append({'skill':skill, 'code':code})
    root = plugin_root / 'skills'
    surface = json.loads((root / 'skill-surface-registry.v4.json').read_text(encoding='utf-8'))
    if surface['digest'] != digest({k:v for k,v in surface.items() if k != 'digest'}):
        issue('*', 'catalog-digest-mismatch')
    expected_names = {item.skill for item in WORKFLOWS}
    actual_names = {path.parent.name for path in root.glob('*/SKILL.md')}
    if (not expected_names <= actual_names if active_surface else actual_names != expected_names):
        issue('*', 'skill-set-mismatch')
    if {item['name'] for item in surface['skills']} != expected_names:
        issue('*', 'catalog-skill-set-mismatch')
    if len(surface['skills']) != len(expected_names) or surface['skill_count'] != len(expected_names):
        issue('*', 'catalog-skill-count-mismatch')
    records = {item['name']:item for item in surface['skills']}
    declared = {row['path'] for item in surface['skills'] for row in item['members']}
    if len(declared) != sum(len(item['members']) for item in surface['skills']):
        issue('*', 'duplicate-package-member')
    extra_members = []
    for definition in WORKFLOWS:
        name = definition.skill
        folder = root / name
        try:
            text = (folder / 'SKILL.md').read_text(encoding='utf-8')
            if not text.startswith('---\n'):
                raise ValueError('frontmatter')
            metadata = yaml.safe_load(text.split('---\n', 2)[1])
            if metadata.get('name') != name or metadata.get('description') != definition.description:
                issue(name, 'discovery-metadata-mismatch')
            ui = yaml.safe_load((folder / 'agents/openai.yaml').read_text(encoding='utf-8'))
            if ui['interface']['display_name'] != definition.title or ui['interface']['default_prompt'] != definition.default_prompt:
                issue(name, 'ui-metadata-mismatch')
            if not 25 <= len(ui['interface']['short_description']) <= 64:
                issue(name, 'ui-description-length')
            actual = json.loads((folder / 'references/actions.json').read_text(encoding='utf-8'))
            if actual != skill_action_reference(registry, definition.name):
                issue(name, 'live-action-reference-mismatch')
            mandatory = {f'skills/{name}/{value}' for value in ('SKILL.md', 'references/actions.json', 'agents/openai.yaml')}
            if definition.name == 'lifecycle':
                mandatory.add(f'skills/{name}/references/shared-boundaries.md')
            selected = records[name]['members']
            required = {row['path'] for row in selected}
            if not mandatory <= required:
                issue(name, 'active-member-set-mismatch')
            for member in required - mandatory:
                resource = (plugin_root / member).resolve()
                if (not resource.is_relative_to((folder / 'references').resolve())
                        or resource.suffix not in {'.md', '.json'}):
                    issue(name, 'invalid-procedure-member')
            all_members = _members(folder, plugin_root)
            extras = [row['path'] for row in all_members if row['path'] not in required]
            extra_members.extend(extras)
            if [row for row in all_members if row['path'] in required] != selected:
                issue(name, 'package-member-integrity')
            if extras and not active_surface:
                issue(name, 'unreferenced-files-pending-final-purge')
            actions = [item['name'] for item in registry.schemas() if item['workflow'] == definition.name]
            if records[name].get('workflow') != definition.name:
                issue(name, 'workflow-owner-mismatch')
            if records[name].get('source_skill') != definition.source_skill:
                issue(name, 'original-skill-provenance-mismatch')
            if records[name]['actions'] != actions:
                issue(name, 'action-membership-mismatch')
            # Follow the entrypoint's actual reference graph. Declaring a file
            # in the manifest does not make otherwise unreachable guidance usable.
            reachable = set()
            pending = [folder / 'SKILL.md']
            while pending:
                document = pending.pop()
                identity = document.relative_to(plugin_root).as_posix()
                if identity in reachable:
                    continue
                reachable.add(identity)
                if document.suffix != '.md':
                    continue
                for reference in re.findall(r'\[[^\]]+\]\(([^)]+)\)', document.read_text(encoding='utf-8')):
                    if '://' in reference or reference.startswith('#'):
                        continue
                    target = (document.parent / reference.split('#',1)[0]).resolve()
                    if (not target.is_relative_to(root.resolve()) or not target.is_file()
                            or target.relative_to(plugin_root).as_posix() not in declared):
                        issue(name, 'standalone-reference-unavailable')
                    else:
                        pending.append(target)
            if (required - {f'skills/{name}/agents/openai.yaml'}) - reachable:
                issue(name, 'unreachable-procedure-member')
        except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
            issue(name, 'invalid-skill-package')
    mirror = json.loads((plugin_root / 'schemas/skills/skill-registry.v4.json').read_text(encoding='utf-8'))
    if mirror != surface:
        issue('*', 'schema-projection-mismatch')
    workflows = json.loads((plugin_root / 'sdk/workflows/skill-workflow-registry.v4.json').read_text(encoding='utf-8'))
    if workflows.get('workflows') != registry.workflow_schemas() or workflows.get('skill_surface_digest') != surface['digest']:
        issue('*', 'sdk-projection-mismatch')
    return {'schema':'evidence-lane.governed-skill-audit.v4', 'status':'PASS' if not issues else 'FAIL',
            'scope': 'retained_active_surface' if active_surface else 'complete_skill_package',
            'skill_count':len(expected_names), 'physical_skill_count':len(actual_names),
            'registry_skill_count':len(expected_names), 'issues':issues,
            'retired_skill_directories': sorted(actual_names - expected_names),
            'unreferenced_retained_skill_files': sorted(extra_members),
            'workflows_without_actions': [row['name'] for row in registry.workflow_schemas() if not row['actions']],
            'native_prompt_verified':False, 'installation_verified':False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plugin-root', type=Path, default=PLUGIN_ROOT)
    parser.add_argument('--active-surface', action='store_true')
    args = parser.parse_args()
    result = audit(args.plugin_root, active_surface=args.active_surface)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())

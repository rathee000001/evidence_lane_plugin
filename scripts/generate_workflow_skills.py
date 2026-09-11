"""Maintainer-only projection of the engine registry into standalone skill packages."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugins/evidence-lane-plugin/src'))
sys.path.insert(0, str(ROOT / 'scripts'))

from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.registry import WORKFLOWS
from evidence_lane_plugin.workflow_surface import digest
from evidence_lane_plugin.workflow_surface import skill_action_reference as reference
from workflow_skill_bodies import REFERENCES, SHARED, body


def write_bytes(path, content, *, check=False):
    if path.is_file() and path.read_bytes() == content:
        return
    if check:
        raise RuntimeError('Stale skill projection: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def write_json(path, value, *, check=False):
    # Shared action references must have the same bytes as the package schema
    # compiler; different key order otherwise makes each generator stale the other.
    write_bytes(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode(), check=check)


def generate(registry, plugin, *, check=False):
    """Adapt retained first-class skills; do not install or execute project work."""
    if {'start', 'work', 'query', 'connect', 'continue'} & {row.name for row in WORKFLOWS}:
        raise RuntimeError('The six-workflow prototype cannot generate the product skill tree.')
    lifecycle_skill = next(item.skill for item in WORKFLOWS if item.name == 'lifecycle')
    shared_path = plugin / 'skills' / lifecycle_skill / 'references/shared-boundaries.md'
    write_bytes(shared_path, SHARED.encode(), check=check)
    skills = []
    for workflow in WORKFLOWS:
        folder = plugin / 'skills' / workflow.skill
        entry = folder / 'SKILL.md'
        write_bytes(entry, ('---\nname: ' + workflow.skill + '\ndescription: '
                    + json.dumps(workflow.description) + '\n---\n' + body(workflow, lifecycle_skill=lifecycle_skill)).encode(), check=check)
        action_path = folder / 'references/actions.json'
        write_json(action_path, reference(registry, workflow.name), check=check)
        # These are new interface-only files. No existing policy or dependency
        # overrides are replaced; later standalone install uses the same name.
        metadata = 'interface:\n' + ''.join('  ' + key + ': ' + json.dumps(value) + '\n'
            for key, value in {'display_name': workflow.title,
                               'short_description': workflow.short_description,
                               'default_prompt': workflow.default_prompt}.items())
        metadata += 'dependencies:\n  tools:\n    - type: "mcp"\n      value: "evidence-lane"\n      description: "Evidence Lane v4 engine connection"\n'
        meta_path = folder / 'agents/openai.yaml'
        if meta_path.exists():
            # Preserve unrelated top-level blocks byte-for-byte, including their
            # quoting and invocation/dependency policy. The initializer emits a
            # plain interface mapping; custom block shapes are rejected visibly.
            import yaml
            original = meta_path.read_text(encoding='utf-8')
            previous = yaml.safe_load(original) or {}
            if not isinstance(previous, dict):
                raise ValueError('UI metadata must be a mapping')
            blocks = re.split(r'(?m)^(?=[A-Za-z_][A-Za-z0-9_]*:)', original)
            preserved = [block for block in blocks if re.match(r'^[A-Za-z_][A-Za-z0-9_]*:', block)
                         and not block.startswith('interface:')]
            if len(preserved) != len(previous.keys() - {'interface'}):
                raise ValueError('Inspect custom skill metadata before updating its interface')
            if preserved:
                metadata = metadata.split('dependencies:', 1)[0]
                if 'dependencies' not in previous:
                    metadata += 'dependencies:\n  tools:\n    - type: "mcp"\n      value: "evidence-lane"\n      description: "Evidence Lane v4 engine connection"\n'
                metadata += ''.join(preserved)
        write_bytes(meta_path, metadata.encode(), check=check)
        active_members = [entry, action_path, meta_path]
        for key, content in sorted(REFERENCES.items()):
            owner, filename = key.split('/', 1)
            if owner == workflow.name:
                resource = folder / 'references' / filename
                write_bytes(resource, content.encode(), check=check)
                active_members.append(resource)
        if workflow.name == 'lifecycle':
            active_members.append(shared_path)
        members = [{'path': path.relative_to(plugin).as_posix(), 'bytes': path.stat().st_size,
                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                   for path in sorted(active_members)]
        skills.append({'name': workflow.skill, 'workflow': workflow.name,
                       'description': workflow.description, 'members': members,
                       'actions': [item['name'] for item in registry.schemas() if item['workflow'] == workflow.name],
                       'source_skill': workflow.source_skill})
    result = {'schema': 'evidence-lane.skill-surface-registry.v4',
              'authority': 'engine_typed_action_registry', 'skill_count': len(skills), 'skills': skills,
              'native_installation_verified': False, 'standalone_prompt_verified': False}
    result['digest'] = digest(result)
    write_json(plugin / 'skills/skill-surface-registry.v4.json', result, check=check)
    write_json(plugin / 'schemas/skills/skill-registry.v4.json', result, check=check)
    write_json(plugin / 'sdk/workflows/skill-workflow-registry.v4.json', {
        'schema': 'evidence-lane.skill-workflow-registry.v4', 'authority': 'engine_typed_action_registry',
        'workflows': registry.workflow_schemas(), 'skill_surface_digest': result['digest']}, check=check)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin-root', type=Path, default=ROOT / 'plugins/evidence-lane-plugin')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='evi-skill-registry-') as temp:
        registry = Engine(Path(temp)).registry
        registry.freeze()
        result = generate(registry, args.plugin_root, check=args.check)
    print(json.dumps({'skill_count': result['skill_count'], 'actions': sum(len(item['actions']) for item in result['skills']),
                      'digest': result['digest'], 'native_prompt_verified': False}))


if __name__ == '__main__':
    main()

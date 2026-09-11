"""Compile exact baseline refreshes into existing parser and retirement tasks."""
from __future__ import annotations

import os
from pathlib import Path

from .errors import LaneError
from .plan_runtime import TaskDefinition, TaskOperation
from .source_selectors import SnapshotRetire


def read_baselines(engine, store, request, capture):
    result, seen, files = [], set(), 0
    for selected in request.baseline_snapshots:
        key = (selected.lane_id, selected.snapshot_id)
        if key in seen:
            raise LaneError('SOURCE_REFRESH_DUPLICATE_BASELINE', 'Select each exact baseline once.')
        seen.add(key)
        body = engine.registry.selector_owner(selected.lane_id).snapshot(store, selected.snapshot_id)
        if body['head_snapshot'] != selected.snapshot_id:
            raise LaneError('SOURCE_REFRESH_BASELINE_CHANGED', 'Select the current retained lineage head, including an explicitly retired head for reappearance.')
        files += len(body['files'])
        if files > request.max_files:
            raise LaneError('SOURCE_REFRESH_BASELINE_BUDGET', 'The complete baseline inventory exceeds this preparation file budget.')
        for relative in {*body['paths'], *(item['path'] for item in body['files'])}:
            capture.boundary(store.source_root / relative)
        result.append(body)
    return result


def bind_refresh(arguments, spec, paths, baselines):
    matches = [item for item in baselines if item['lane_id'] == arguments.lane_id
        and sorted(item['paths']) == sorted(paths)]
    if len(matches) > 1:
        raise LaneError('SOURCE_REFRESH_AMBIGUOUS_BASELINE', 'Select one retained head for each exact input scope.')
    if not matches:
        return arguments, None
    values = arguments.model_dump(mode='json')
    values['expected_snapshot'] = matches[0]['snapshot_id']
    return spec.input_model.model_validate(values), matches[0]


def retirement_tasks(engine, store, context, request, tasks, files, baselines, rebound, capture):
    """Remaining exact scopes retire only after every live file has a parser task.

    Task identities resolve to their verified results at retirement execution;
    unknown future snapshot IDs are never guessed or injected into a Plan.
    """
    result, changes = [], []
    prepared = {row['path']: row for row in files}
    for baseline in baselines:
        key = (baseline['lane_id'], baseline['snapshot_id'])
        common = {'lane_id': baseline['lane_id'], 'snapshot_id': baseline['snapshot_id']}
        if key in rebound:
            changes.append({**common, 'disposition': 'reindexed', 'task_id': rebound[key]})
            continue
        if baseline['retired']:
            changes.append({**common, 'disposition': 'already_retired', 'proof_object': baseline['retirement']['proof_object']})
            continue
        git = None
        if baseline['byte_source'] == 'git_commit_blobs':
            from .source_git_selectors import observe_checkpoint
            def path(value):
                selected = store.source_root / value
                capture.boundary(selected)
                return selected
            git = observe_checkpoint(store, request.git_snapshot_id, path, request.max_files)
        replacement_ids, permitted = set(), set(baseline['paths'])
        for row in baseline['files']:
            path = store.source_root / row['path']
            capture.boundary(path)
            permitted.add(row['path'])
            if row['path'] in git['checkpoint']['blob_ids'] if git else path.exists():
                if row['path'] not in prepared:
                    raise LaneError('SOURCE_REFRESH_INCOMPLETE_SCOPE', 'A still-present baseline source is outside the new selection; preserve that scope or include its complete replacement.',
                        details={'path': row['path'], 'lane_id': baseline['lane_id']})
                replacement_ids.add(prepared[row['path']]['task_id'])
        task_id = 'source-' + context.request_id + '-' + str(len(tasks) + len(result) + 1)
        spec = engine.registry.get('source_snapshot_retire')
        values = SnapshotRetire(**common, replacement_tasks=sorted(replacement_ids),
            git_snapshot_id=request.git_snapshot_id if git else None,
            max_files=min(4096, request.max_files), max_total_bytes=min(536_870_912, request.max_total_bytes))
        if git:
            permitted.add('.')
        # Only immutable manifest paths are read by this operation. Large
        # companion-file packages use their common parent as the path grant.
        if len(permitted) > 128:
            permitted = {Path(os.path.commonpath([str(Path(path).parent) for path in permitted])).as_posix()}
        result.append(TaskDefinition(task_id=task_id, title='Retire replaced source scope: ' + baseline['lane_id'],
            requested_outcome='Verify every former source is absent or preserved by a current replacement, then retire this exact selector while retaining its history.',
            profile=spec.profile, permitted_paths=sorted(permitted), permitted_tools=['Python', 'Git'] if git else ['Python'],
            allowed_actions=[spec.name], acceptance_checks=list(spec.verification_checks), budget=request.budget,
            plan_group='sources-' + context.request_id,
            operation=TaskOperation(action=spec.name, arguments=values.model_dump(mode='json'))))
        changes.append({**common, 'disposition': 'retired', 'task_id': task_id})
    return result, changes

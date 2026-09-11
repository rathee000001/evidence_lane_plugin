"""Exact files belonging to one coherent, published set of lane databases.

Only registry-owned files are admitted. A project evidence head coordinator read pins every lane while
bytes are copied; lane database bytes must remain identical to their head hash.
"""
from __future__ import annotations

import json
import re

from .errors import LaneError
from .lanes import (
    AUTHORITY_LANE_IDS,
    CANONICAL_LANE_IDS,
    MAX_CUSTOM_INSTANCES,
    get_lane,
    is_named_custom_lane,
)
from .storage import DATABASE_NAME, LANE_APPLICATION_ID, STORAGE_LAYOUT
from .store import DIGEST, assert_quiescent, has_table
from .universe_snapshot import root_reference


def relative_file(value):
    from pathlib import Path
    if value == DATABASE_NAME:
        return Path(value)
    if not isinstance(value, str):
        raise LaneError('RECOVERY_FILE_SCOPE', 'A recorded file lacks its canonical relative path.')
    if re.fullmatch(r'authorities/canon/files/(?:graph|outbox|inbox)/[0-9a-f]{64}/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\.json', value):
        return Path(value)
    parts = value.split('/')
    named = (parts[1],) if len(parts) >= 3 and parts[0] == 'sectors' and is_named_custom_lane(parts[1]) else ()
    for lane_id in (*CANONICAL_LANE_IDS, *named):
        lane = get_lane(lane_id)
        if value == lane.database_relative_path:
            return Path(value)
        patterns = (
            re.escape(lane.files_relative_path) + r'/[0-9a-f]{2}/[0-9a-f]{62}',
            re.escape(lane.schema_history_relative_path) + r'/[a-z][a-z0-9]{0,31}\.[1-9][0-9]*\.[0-9a-f]{64}\.json',
            re.escape(lane.folder) + r'/[a-z][a-z0-9_]{0,47}/[0-9a-f]{64}/[a-z][a-z0-9_.-]{0,95}',
        )
        if any(re.fullmatch(pattern, value) for pattern in patterns):
            return Path(value)
    raise LaneError('RECOVERY_FILE_SCOPE', 'A recorded file lies outside the canonical lane storage contract.')


def inventory(project, *, tick=lambda: None, require_quiescent=True):
    """Caller holds a published project snapshot for this complete operation."""
    from .database_recovery import MAX_BYTES, MAX_FILES, _hash, _integrity
    from .lane_traversal import validate_snapshot_contract
    from .plan_runtime import content_digest
    from .storage import reject_links

    selected = {}
    total = 0

    def add(path, digest, size):
        nonlocal total
        relative_file(path)
        if not isinstance(digest, str) or not re.fullmatch(DIGEST, digest) or type(size) is not int or not 0 <= size <= 256 * 1024 * 1024:
            raise LaneError('BACKUP_FILE_IDENTITY', 'A recorded file has an invalid bounded identity.')
        value = {'path': path, 'sha256': digest, 'bytes': size}
        if path in selected and selected[path] != value:
            raise LaneError('BACKUP_FILE_COLLISION', 'Two records disagree about one file.')
        if path not in selected:
            total += size
        selected[path] = value
        if len(selected) > MAX_FILES or total > MAX_BYTES:
            raise LaneError('BACKUP_CONTENT_BUDGET', 'The project exceeds its recovery file or byte budget.')
        tick()

    def rows(connection, sql, arguments=()):
        result = connection.execute(sql + ' LIMIT ?', (*arguments, MAX_FILES + 1)).fetchall()
        if len(result) > MAX_FILES:
            raise LaneError('BACKUP_CONTENT_BUDGET', 'A lane registry exceeds the recovery record budget.')
        tick()
        return result

    def bounded_json(value, limit):
        if not isinstance(value, str) or len(value.encode()) > limit:
            raise LaneError('BACKUP_RECORD_BUDGET', 'A lane record exceeds its bounded storage contract.')
        return json.loads(value)

    project.assert_current_binding()
    head = root_reference(project)
    with project.connection(read_only=True) as connection:
        metadata = _integrity(connection, project.project_id)
    root_identity = _hash(project.database, project.root)
    add(DATABASE_NAME, root_identity['sha256'], root_identity['bytes'])
    catalog = project.lane_catalog()
    if not (set(AUTHORITY_LANE_IDS) <= {row['lane_id'] for row in catalog}
            and len(catalog) <= len(CANONICAL_LANE_IDS) + MAX_CUSTOM_INSTANCES):
        raise LaneError('BACKUP_LANE_CATALOG', 'The project lacks its retained authority catalog.')
    for entry in catalog:
        tick()
        lane = project.lane(entry['lane_id'])  # Validates catalog, identity and published head.
        identity = _hash(lane.database, project.root)
        add(lane.definition.database_relative_path, identity['sha256'], identity['bytes'])
        with lane.connection(read_only=True) as connection:
            _integrity(connection, project.project_id, lane=lane)
            if lane.lane_id == 'plan' and require_quiescent:
                assert_quiescent(connection)
            for item in rows(connection, 'SELECT digest,size_bytes FROM objects ORDER BY digest'):
                path = lane.object_path(item['digest']).relative_to(project.root).as_posix()
                add(path, item['digest'], item['size_bytes'])
            if lane.lane_id == 'canon' and has_table(connection, 'canon_task_edges'):
                from .canon_task_graph import CanonStore
                canon = CanonStore(project)
                for item in rows(connection, 'SELECT edge_id FROM canon_task_edges ORDER BY sequence'):
                    edge = canon._edge(connection, item['edge_id'])
                    path = project.root / edge.artifact_path
                    add(edge.artifact_path, edge.edge_digest, path.stat().st_size)
            if lane.lane_id == 'canon' and has_table(connection, 'canon_exchanges'):
                from .canon_task_graph import CanonStore
                canon = CanonStore(project)
                for item in rows(connection, 'SELECT exchange_id FROM canon_exchanges ORDER BY sequence'):
                    packet, _ = canon.exchange(connection, item['exchange_id'])
                    if packet['artifact_path']:
                        path = project.root / packet['artifact_path']
                        add(packet['artifact_path'], packet['envelope_digest'], path.stat().st_size)
            migrations = {(item['owner'], item['version']): item['digest'] for item in rows(connection,
                'SELECT owner,version,digest FROM schema_migrations ORDER BY owner,version')}
            history = rows(connection, 'SELECT * FROM schema_history_files ORDER BY owner,version') if has_table(connection, 'schema_history_files') else []
            if set(migrations) != {(item['owner'], item['version']) for item in history}:
                raise LaneError('BACKUP_SCHEMA_HISTORY', 'Every lane migration must retain its exact schema history file.')
            for item in history:
                relative = lane.definition.schema_history_relative_path + '/' + item['filename']
                path = project.root / relative_file(relative)
                reject_links(path, project.root)
                if item['filename'] != f"{item['owner']}.{item['version']}.{item['digest']}.json" or path.stat().st_size > 4194304:
                    raise LaneError('BACKUP_SCHEMA_HISTORY', 'A schema history reference is invalid or too large.')
                document = bounded_json(path.read_text(encoding='utf-8'), 4194304)
                if (content_digest(document) != item['digest'] or document['project_id'] != project.project_id
                        or document['lane_id'] != lane.lane_id or document['owner'] != item['owner']
                        or document['version'] != item['version']
                        or document['migration_digest'] != migrations[(item['owner'], item['version'])]):
                    raise LaneError('BACKUP_SCHEMA_HISTORY', 'The schema history file differs from its lane migration.')
                add(relative, item['digest'], path.stat().st_size)
            if not has_table(connection, 'views_snapshots'):
                continue
            snapshots = {}
            for item in rows(connection, 'SELECT * FROM views_snapshots ORDER BY view_id,generation'):
                body = bounded_json(item['body_json'], 1048576)
                contract_row = connection.execute('SELECT body_json FROM views_contracts WHERE contract_digest=? AND view_id=?',
                    (body['binding']['contract_digest'], item['view_id'])).fetchone()
                if not contract_row:
                    raise LaneError('BACKUP_VIEW_INTEGRITY', 'A historical lane snapshot lacks its original contract.')
                contract = bounded_json(contract_row[0], 131072)
                validate_snapshot_contract(project.project_id, contract, body)
                if (content_digest(body) != item['snapshot_digest'] or body['binding']['lane_id'] != lane.lane_id
                        or body['generation'] != item['generation'] or contract['folder'] != lane.definition.folder):
                    raise LaneError('BACKUP_VIEW_INTEGRITY', 'A historical snapshot differs from its owning lane identity.')
                snapshots[(item['view_id'], item['snapshot_digest'])] = item['generation']
                for file in body['files']:
                    relative = contract['folder'] + '/' + item['view_id'].split('.')[1] + '/' + item['snapshot_digest'] + '/' + file['filename']
                    add(relative, file['sha256'], file['bytes'])
            for current in rows(connection, 'SELECT * FROM views_current ORDER BY view_id'):
                if snapshots.get((current['view_id'], current['snapshot_digest'])) != current['generation']:
                    raise LaneError('BACKUP_VIEW_INTEGRITY', 'A current selector lacks its exact recorded snapshot.')
    return {'root_pv': head, 'lanes': catalog, 'source_root': metadata['source_root'],
            'files': sorted(selected.values(), key=lambda item: item['path'])}


def lane_identity(connection, project_id, lane):
    if connection.execute('PRAGMA application_id').fetchone()[0] != LANE_APPLICATION_ID:
        raise LaneError('BACKUP_LANE_IDENTITY', 'The recovery database is not a v4 lane.')
    row = connection.execute('SELECT * FROM lane_identity WHERE singleton=1').fetchone()
    if row is None or any(row[key] != value for key, value in {
            'project_id': project_id, 'lane_id': lane.lane_id,
            'kind': lane.definition.kind, 'storage_layout': STORAGE_LAYOUT}.items()):
        raise LaneError('BACKUP_LANE_IDENTITY', 'The recovery database belongs to another lane or project.')
    return dict(row)

"""Optional source retirement history, owned separately inside each sector DB.

Adding this owner never changes a parser's existing migration history. Queries
validate its complete history when present and never initialize it implicitly.
"""
from __future__ import annotations

import re

from .errors import LaneError
from .lanes import get_lane, lane_family
from .migrations import Migration, read_compatibility


def selector_migrations(lane_id):
    lane = get_lane(lane_id)
    owner = lane_family(lane_id).replace('_', '') + 'selector'
    if lane.kind != 'sector' or owner not in lane.schema_owners:
        raise LaneError('SOURCE_SELECTOR_OWNER_UNAVAILABLE', 'This lane has no source selector schema owner.')
    return (Migration(owner, 1, 'Retain source retirement proofs without deleting lineage heads', (
        '''CREATE TABLE selector_retirement(
            snapshot_id TEXT PRIMARY KEY REFERENCES objects(digest),
            proof_object TEXT NOT NULL REFERENCES objects(digest),
            job_id TEXT NOT NULL, plan_revision INTEGER NOT NULL,
            created_at TEXT NOT NULL) STRICT''',
    )),)


def selector_initialized(lane):
    return lane is not None and read_compatibility(lane, selector_migrations(lane.lane_id))[0]['status'] == 'compatible'


def active_selector_sql(lane, alias='c'):
    if not re.fullmatch(r'[a-z][a-z0-9_]*', alias):
        raise LaneError('INVALID_SELECTOR_ALIAS', 'Use an internal SQL alias.')
    if not selector_initialized(lane):
        return '1'
    return f'NOT EXISTS (SELECT 1 FROM selector_retirement r WHERE r.snapshot_id={alias}.snapshot_id)'


def is_retired(lane, snapshot_id):
    if snapshot_id is None or not selector_initialized(lane):
        return False
    with lane.connection(read_only=True) as connection:
        return connection.execute('SELECT 1 FROM selector_retirement WHERE snapshot_id=?', (snapshot_id,)).fetchone() is not None

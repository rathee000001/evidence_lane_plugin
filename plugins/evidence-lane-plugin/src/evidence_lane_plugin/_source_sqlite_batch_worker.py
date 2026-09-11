"""Internal Sources reader. It receives no project-state database or writer."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def main() -> None:
    path = Path.cwd() / 'request.json'
    if len(sys.argv) != 2 or not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('SOURCE_SQLITE_BATCH_REQUEST_INVALID')
    with path.open('rb') as stream:
        raw = stream.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != sys.argv[1]:
        raise ValueError('SOURCE_SQLITE_BATCH_REQUEST_INVALID')
    request = json.loads(raw)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evidence_lane_plugin.hashing import canonical_json_bytes, sha256_bytes
    from evidence_lane_plugin.source_sqlite import (
        SQLiteBatchBudget,
        _batch_asset,
        _batch_programs,
        _collect_sqlite_inspections,
    )
    if (set(request) != {'schema', 'assets', 'image_limits', 'batch_limits', 'programs'}
            or request['schema'] != 'evidence-lane.sqlite-batch-request.v4'
            or request['programs'] != _batch_programs()
            or set(request['batch_limits']) != set(SQLiteBatchBudget().contract())
            or set(request['image_limits']) != {'max_embedded_member_bytes', 'exact_count_max_database_bytes'}):
        raise ValueError('SOURCE_SQLITE_BATCH_REQUEST_INVALID')
    budget = SQLiteBatchBudget(**request['batch_limits'])
    if not isinstance(request['assets'], list) or len(request['assets']) > budget.max_assets:
        raise ValueError('SOURCE_SQLITE_BATCH_ASSET_BUDGET')
    limits = request['image_limits']
    if (type(limits['max_embedded_member_bytes']) is not int or not 1 <= limits['max_embedded_member_bytes'] <= 768 * 1024 * 1024
            or type(limits['exact_count_max_database_bytes']) is not int or not 1 <= limits['exact_count_max_database_bytes'] <= 32 * 1024 * 1024):
        raise ValueError('SOURCE_SQLITE_BATCH_REQUEST_INVALID')
    assets = [_batch_asset(value) for value in request['assets']]
    result = _collect_sqlite_inspections(assets, **limits, batch_budget=budget)
    if len(canonical_json_bytes(result)) > budget.max_metadata_bytes:
        raise ValueError('SOURCE_SQLITE_BATCH_METADATA_BUDGET')
    response = {'status': 'ok', 'request_sha256': sys.argv[1],
        'program_sha256': sha256_bytes(canonical_json_bytes(request['programs'])).lower(), 'result': result}
    sys.stdout.buffer.write(canonical_json_bytes(response))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:  # noqa: BLE001 - bounded worker failures never expose source paths or payloads
        code = getattr(error, 'code', None) or (str(error) if isinstance(error, ValueError) else 'SOURCE_SQLITE_BATCH_WORKER_FAILED')
        if not isinstance(code, str) or not code.startswith('SOURCE_SQLITE_'):
            code = 'SOURCE_SQLITE_BATCH_WORKER_FAILED'
        print(json.dumps({'status': 'error', 'error_code': code}))
        raise SystemExit(1) from None

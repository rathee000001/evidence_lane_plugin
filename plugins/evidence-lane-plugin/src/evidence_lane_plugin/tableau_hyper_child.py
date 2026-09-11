"""Private JSON worker for the pinned Hyper API; never accepts arbitrary SQL."""
from __future__ import annotations

import base64
import datetime
import decimal
import hashlib
import json
import math
import sys
import tempfile
from pathlib import Path

VERSION = '0.0.26359'


def runtime_evidence():
    import tableauhyperapi
    if tableauhyperapi.__version__ != VERSION:
        raise ValueError('TABLEAU_HYPER_VERSION_MISMATCH')
    folder = Path(tableauhyperapi.__file__).parent
    files = {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob('*')) if p.is_file() and p.suffix not in {'.pyc', '.log'} and '__pycache__' not in p.parts}
    return {'engine': 'tableauhyperapi', 'version': VERSION,
        'runtime_files_sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        'runtime_file_count': len(files), 'telemetry': False, 'tcp_listener': False,
        'queries': 'catalog_count_and_bounded_sample_only', 'source': 'private_copy', 'read_only_mode_claimed': False}


def value_json(value):
    if value is None or isinstance(value, (bool, int, str)):
        result = value
    elif isinstance(value, float):
        result = value if math.isfinite(value) else {'type': 'double', 'value': str(value)}
    elif isinstance(value, decimal.Decimal):
        result = {'type': 'numeric', 'value': str(value)}
    elif isinstance(value, (bytes, bytearray, memoryview)):
        result = {'type': 'binary', 'content_base64': base64.b64encode(value).decode('ascii')}
    else:
        result = {'type': type(value).__name__, 'value': str(value)}
    if len(json.dumps(result, ensure_ascii=False)) > 32768:
        raise ValueError('TABLEAU_HYPER_VALUE_BUDGET')
    return result


def inspect_connection(connection, max_rows):
    result = {kind: [] for kind in ('hyper_schema', 'hyper_table', 'hyper_column', 'hyper_row')}
    schemas = sorted(connection.catalog.get_schema_names(), key=str)
    if len(schemas) > 64:
        raise ValueError('TABLEAU_HYPER_SCHEMA_BUDGET')
    cells = 0
    for schema in schemas:
        schema_name = schema.name.unescaped
        result['hyper_schema'].append({'name': schema_name})
        for table in sorted(connection.catalog.get_table_names(schema), key=str):
            if len(result['hyper_table']) >= 128:
                raise ValueError('TABLEAU_HYPER_TABLE_BUDGET')
            definition = connection.catalog.get_table_definition(table)
            if len(definition.columns) > 256:
                raise ValueError('TABLEAU_HYPER_COLUMN_BUDGET')
            identity = {'schema_name': schema_name, 'table_name': table.name.unescaped}
            columns = [{'name': column.name.unescaped, 'sql_type': str(column.type), 'nullability': str(column.nullability)}
                for column in definition.columns]
            result['hyper_column'].extend({**identity, 'column_ordinal': i, **column} for i, column in enumerate(columns))
            count = connection.execute_scalar_query(f'SELECT COUNT(*) FROM {table}')
            sampled = 0
            # An explicit projection prevents row-function interpretation and quotes every identifier.
            with connection.execute_query(f'SELECT * FROM {table} LIMIT {max_rows}') as cursor:
                for row in cursor:
                    cells += len(row)
                    if cells > 50_000:
                        raise ValueError('TABLEAU_HYPER_CELL_BUDGET')
                    result['hyper_row'].append({**identity, 'row_ordinal': sampled, 'values': [value_json(v) for v in row]})
                    sampled += 1
            result['hyper_table'].append({**identity, 'name': table.name.unescaped,
                'row_count': count, 'sample_rows': sampled, 'rows_truncated': sampled < count,
                'sample_order': 'vendor_scan_order_not_a_stable_primary_key', 'columns': columns})
    return result


def generate_database(connection, tables):
    from tableauhyperapi import Inserter, Nullability, SqlType, TableDefinition, TableName
    for item in tables:
        connection.catalog.create_schema_if_not_exists(item['schema_name'])
        columns = []
        for column in item['columns']:
            kind = column['type']
            sql_type = SqlType.numeric(column['precision'], column['scale']) if kind == 'numeric' else getattr(SqlType, kind)()
            columns.append(TableDefinition.Column(column['name'], sql_type,
                Nullability.NULLABLE if column['nullable'] else Nullability.NOT_NULLABLE))
        table = TableDefinition(TableName(item['schema_name'], item['name']), columns)
        connection.catalog.create_table(table)
        rows = []
        for row in item['rows']:
            converted = []
            for column, value in zip(item['columns'], row):
                kind = column['type']
                if value is not None:
                    if kind == 'numeric':
                        value = decimal.Decimal(str(value))
                    elif kind == 'date':
                        value = datetime.date.fromisoformat(value)
                    elif kind == 'timestamp':
                        value = datetime.datetime.fromisoformat(value)
                    elif kind == 'text' and not isinstance(value, str) or kind == 'bool' and type(value) is not bool or kind == 'big_int' and type(value) is not int or kind == 'double' and type(value) not in (int, float):
                        raise ValueError('TABLEAU_HYPER_TYPED_VALUE')
                converted.append(value)
            rows.append(converted)
        with Inserter(connection, table) as inserter:
            inserter.add_rows(rows)
            inserter.execute()


def run(request):
    from tableauhyperapi import Connection, CreateMode, HyperProcess, Telemetry
    evidence = runtime_evidence()
    with tempfile.TemporaryDirectory(prefix='evidence-lane-hyper-') as temporary:
        folder, results = Path(temporary), []
        with HyperProcess(Telemetry.DO_NOT_SEND_USAGE_DATA_TO_TABLEAU,
                parameters={'log_config': '', 'use_tcp_port': 'off'}) as hyper:
            if request['operation'] == 'generate':
                from evidence_lane_plugin.tableau_contracts import TableauGenerate
                spec = TableauGenerate.model_validate(request['spec'])
                path = folder / 'generated.hyper'
                with Connection(hyper.endpoint, path, CreateMode.CREATE) as connection:
                    generate_database(connection, [item.model_dump() for item in spec.tables])
                if path.stat().st_size > 8_388_608:
                    raise ValueError('TABLEAU_HYPER_FILE_BUDGET')
                content = path.read_bytes()
                return {'content_base64': base64.b64encode(content).decode('ascii'), 'evidence': evidence}
            if request['operation'] != 'inspect' or not 0 <= request['max_rows_per_table'] <= 1000 or len(request['files']) > 16:
                raise ValueError('TABLEAU_HYPER_REQUEST_INVALID')
            for i, file in enumerate(request['files']):
                content = base64.b64decode(file['content_base64'], validate=True)
                if len(content) > 8_388_608:
                    raise ValueError('TABLEAU_HYPER_FILE_BUDGET')
                path = folder / f'input-{i}.hyper'
                path.write_bytes(content)
                with Connection(hyper.endpoint, path, CreateMode.NONE) as connection:
                    result = inspect_connection(connection, request['max_rows_per_table'])
                # Hyper may change a database header on open; never point it at the caller's source.
                results.append({'name': file['name'], 'collections': result})
        if runtime_evidence() != evidence:
            raise ValueError('TABLEAU_HYPER_RUNTIME_CHANGED')
        return {'files': results, 'evidence': evidence}


if __name__ == '__main__':
    try:
        raw = sys.stdin.buffer.read(25_165_825)
        if len(raw) > 25_165_824:
            raise ValueError('TABLEAU_HYPER_INPUT_BUDGET')
        # The fixed parent sends these trusted engine roots only after assigning
        # this waiting child to its bounded OS process group. Third-party imports
        # happen inside run(), after this explicit bootstrap.
        request = json.loads(raw)
        sys.path[:0] = request.pop('engine_import_roots')
        response = run(request)
        value = json.dumps(response, ensure_ascii=True, allow_nan=False).encode('utf-8')
        if len(value) > 16_777_216:
            raise ValueError('TABLEAU_HYPER_OUTPUT_BUDGET')
        sys.stdout.buffer.write(value)
    except Exception:  # noqa: BLE001 - redact all private vendor exceptions at this process boundary
        # Vendor exceptions can contain credentials, paths, SQL or source values.
        sys.stderr.write('TABLEAU_HYPER_OPERATION_FAILED')
        sys.exit(1)

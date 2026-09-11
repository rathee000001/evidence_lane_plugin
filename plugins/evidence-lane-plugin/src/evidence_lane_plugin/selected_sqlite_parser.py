"""Bounded adaptation of the original selected-SQLite inspector.

Uses an immutable byte image supplied by the owning worker. Imported schema SQL
is evidence; only fixed metadata queries and selected ordinary tables are read.
"""
from __future__ import annotations

import sqlite3
import time

from .document_parsers import digest
from .tabular_values import typed

MAX_DATABASE_BYTES = 8_388_608
MAX_SCHEMA_OBJECTS = 512
MAX_TABLES = 128
MAX_SELECTED_TABLES = 32
MAX_SELECTED_ROWS = 2000


def _quote(identifier):
    if not isinstance(identifier, str) or not identifier or '\x00' in identifier or len(identifier) > 1024:
        raise ValueError('SELECTED_SQLITE_IDENTIFIER_INVALID')
    return '"' + identifier.replace('"', '""') + '"'


def inspect_sqlite_bytes(facts, content, *, tables=(), rows=100, schema_engine='stdlib'):
    if schema_engine not in {'stdlib', 'sqlalchemy'}:
        raise ValueError('SELECTED_SQLITE_SCHEMA_ENGINE_INVALID')
    if (len(content) < 100 or len(content) > MAX_DATABASE_BYTES
            or not content.startswith(b'SQLite format 3\x00')):
        raise ValueError('SELECTED_SQLITE_BYTES_INVALID')
    if content[18:20] != b'\x01\x01':
        raise ValueError('SELECTED_SQLITE_CHECKPOINTED_DELETE_IMAGE_REQUIRED')
    if (not isinstance(rows, int) or isinstance(rows, bool) or not 0 <= rows <= 200
            or len(tables) > MAX_SELECTED_TABLES or len(set(tables)) != len(tables)):
        raise ValueError('SELECTED_SQLITE_SELECTION_BUDGET')
    for name in tables:
        _quote(name)
    connection = sqlite3.connect(':memory:')
    deadline, vm_steps = time.monotonic() + 5, 0

    def progress():
        nonlocal vm_steps
        vm_steps += 1000
        return vm_steps > 2_000_000 or time.monotonic() >= deadline

    allowed_pragmas = {'table_list', 'table_xinfo', 'foreign_key_list', 'integrity_check',
        'foreign_key_check', 'user_version', 'application_id', 'page_count', 'page_size', 'encoding'}

    def authorize(action, first, second, _database, _trigger):
        if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and str(second).lower() in {'count', 'typeof', 'like'}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and str(first).lower() in allowed_pragmas:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    try:
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.deserialize(content)
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.enable_load_extension(False)
        if hasattr(connection, 'setconfig'):
            connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 131072)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65536)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 256)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        connection.set_progress_handler(progress, 1000)
        connection.set_authorizer(authorize)
        connection.row_factory = sqlite3.Row
        schema = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchmany(MAX_SCHEMA_OBJECTS + 1)
        if len(schema) > MAX_SCHEMA_OBJECTS:
            raise ValueError('SELECTED_SQLITE_SCHEMA_BUDGET')
        metadata = connection.execute('PRAGMA table_list').fetchmany(MAX_SCHEMA_OBJECTS + 1)
        if len(metadata) > MAX_SCHEMA_OBJECTS:
            raise ValueError('SELECTED_SQLITE_TABLE_BUDGET')
        table_types = {row['name']: row['type'] for row in metadata if row['schema'] == 'main'}
        ordinary = [row['name'] for row in schema if row['type'] == 'table' and table_types.get(row['name']) == 'table']
        if len(ordinary) > MAX_TABLES or not set(tables) <= set(ordinary):
            raise ValueError('SELECTED_SQLITE_TABLE_SELECTION_INVALID')
        integrity = [row[0] for row in connection.execute('PRAGMA integrity_check').fetchmany(101)]
        if len(integrity) > 100:
            raise ValueError('SELECTED_SQLITE_INTEGRITY_RESULT_BUDGET')
        foreign_key_errors = [dict(row) for row in connection.execute('PRAGMA foreign_key_check').fetchmany(101)]
        if len(foreign_key_errors) > 100:
            raise ValueError('SELECTED_SQLITE_FOREIGN_KEY_RESULT_BUDGET')
        for ordinal, row in enumerate(schema):
            value = dict(row)
            facts.add('sqlite_schema_object', 'sqlite/schema', ordinal, text=value.get('sql') or '',
                object_type=value['type'], name=value['name'], table=value['tbl_name'],
                sql_sha256=digest((value.get('sql') or '').encode()), imported_sql_executed=False)
        extracted_rows = 0
        counts = []
        for table_ordinal, name in enumerate(ordinary):
            quoted = _quote(name)
            columns = [dict(row) for row in connection.execute(f'PRAGMA table_xinfo({quoted})').fetchmany(257)]
            if len(columns) > 256:
                raise ValueError('SELECTED_SQLITE_COLUMN_BUDGET')
            count = connection.execute(f'SELECT count(*) FROM {quoted}').fetchone()[0]
            counts.append({'table': name, 'rows': count})
            facts.add('sqlite_table', 'sqlite/table', table_ordinal, text=name, name=name,
                rows=count, columns=columns, rows_selected=name in tables)
            foreign_keys = [dict(row) for row in connection.execute(f'PRAGMA foreign_key_list({quoted})').fetchmany(257)]
            if len(foreign_keys) > 256:
                raise ValueError('SELECTED_SQLITE_FOREIGN_KEY_BUDGET')
            for ordinal, foreign_key in enumerate(foreign_keys):
                facts.add('sqlite_relationship', f'sqlite/table/{name}', ordinal, from_table=name,
                    foreign_key=foreign_key)
            if name not in tables or rows == 0:
                continue
            limit = min(rows, MAX_SELECTED_ROWS - extracted_rows)
            if not limit:
                raise ValueError('SELECTED_SQLITE_TOTAL_ROW_BUDGET')
            selected_columns = [column['name'] for column in columns if column['hidden'] == 0]
            keys = [column['name'] for column in sorted(columns, key=lambda row: row['pk']) if column['pk']]
            # Row locators are ordinal within this exact immutable image. They
            # do not pretend to be stable IDs across independent database edits.
            order = ' ORDER BY ' + ','.join(_quote(name) for name in keys) if keys else ''
            cursor = connection.execute('SELECT ' + ','.join(_quote(name) for name in selected_columns)
                + f' FROM {quoted}{order} LIMIT ?', (limit,))
            selected_rows = cursor.fetchmany(limit)
            for ordinal, row in enumerate(selected_rows):
                values = [typed(value) for value in row]
                facts.add('sqlite_row', f'sqlite/table/{name}/rows', ordinal,
                    text=' | '.join(str(value['value']) if value['type'] != 'binary' else '[binary]' for value in values),
                    table=name, columns=selected_columns, values=values, row_locator='ordinal_in_exact_image',
                    order_by=keys or None)
            extracted_rows += len(selected_rows)
        state = {'integrity_ok': integrity == ['ok'], 'foreign_keys_ok': not foreign_key_errors,
            'integrity': integrity, 'foreign_key_errors': foreign_key_errors,
            'tables': counts, 'selected_tables': list(tables), 'extracted_rows': extracted_rows,
            'rows_per_table_limit': rows, 'query_only': True, 'imported_sql_executed': False,
            'virtual_tables_and_views_queried': False, 'database_byte_sha256': digest(content)}
        if schema_engine == 'sqlalchemy':
            from .data_toolchain import inspect_sqlalchemy_sqlite_bytes
            state['schema_reflection'] = inspect_sqlalchemy_sqlite_bytes(content, max_columns=256)
        else:
            state['schema_reflection'] = {'engine': 'sqlite3', 'status': 'NATIVE_METADATA',
                'sqlalchemy_invoked': False, 'source_sha256': digest(content)}
        facts.add('sqlite_receipt', 'sqlite/inspection', 0, **state)
        return state, ['Imported SQLite schema SQL is retained as data and never executed.',
            'Rows are read only from explicitly selected ordinary tables; views and virtual tables are not queried.',
            'Row ordinals refer to the exact database image. Data quality findings do not mutate the source.',
            'This parser requires a complete DELETE-journal database image; live WAL state is not inferred.']
    except sqlite3.Error as error:
        raise ValueError('SELECTED_SQLITE_BOUNDED_INSPECTION_FAILED') from error
    finally:
        connection.close()

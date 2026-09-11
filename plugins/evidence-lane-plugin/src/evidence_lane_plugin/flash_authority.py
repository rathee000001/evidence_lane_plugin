"""Verify the packaged ENV/UOP routing authority before project session entry.

The original hash-locked Flash boundary covers the complete adapted ENV/UOP
operating framework and its current executable consumers. It stores policy,
never project or conversation data.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path, PurePosixPath

from pydantic import JsonValue

from .errors import LaneError
from .registry import Contract
from .storage import json_text, reject_links

FLASH_MANIFEST_SCHEMA = 'evidence-lane.session-flash-manifest.v4'
FLASH_MANIFEST_SHA256 = 'b069ed5521808e90c711f93d5e716be60c91e79e953fe060ce28af6adc4d39de'
FLASH_AUTHORITY_VERSION = 'ENV4_UOP4_FULL_OPERATING_FRAMEWORK'


class FlashStatus(Contract):
    state: str = 'verified'
    authority_version: str = FLASH_AUTHORITY_VERSION
    manifest_digest: str
    action_set_digest: str
    member_count: int
    members: list[dict[str, JsonValue]]
    context: str
    runtime_data_mutated: bool = False
    native_attachment_attested: bool = False


def action_set_digest(registry):
    return hashlib.sha256(json_text(registry.schemas()).encode()).hexdigest()


class SessionFlashAuthority:
    def __init__(self, *, asset_root=None):
        self.asset_root = Path(asset_root) if asset_root else Path(__file__).resolve().parents[2]
        self.asset_root = self.asset_root.resolve(strict=True)
        self.manifest_path = self.asset_root / 'env/SESSION_FLASH_MANIFEST.json'

    def _read(self, name, limit):
        relative = PurePosixPath(name)
        if (not relative.parts or relative.is_absolute() or '..' in relative.parts
                or relative.parts[0] not in {'env', 'uop', 'toolchains'} or '\\' in name):
            raise LaneError('SESSION_FLASH_MEMBER_PATH_INVALID', 'Flash members must stay in their packaged authority folder.')
        path = self.asset_root / name
        reject_links(path, self.asset_root)
        try:
            with path.open('rb') as stream:
                raw = stream.read(limit + 1)
        except OSError:
            raise LaneError('SESSION_FLASH_MEMBER_MISSING', 'A locked Flash member is unavailable.') from None
        if len(raw) > limit:
            raise LaneError('SESSION_FLASH_MEMBER_BUDGET', 'A locked Flash member exceeds its byte budget.')
        return raw

    def verify(self, *, registry=None):
        raw = self._read('env/SESSION_FLASH_MANIFEST.json', 262144)
        if hashlib.sha256(raw).hexdigest() != FLASH_MANIFEST_SHA256:
            raise LaneError('SESSION_FLASH_MANIFEST_HASH_MISMATCH', 'The Flash manifest differs from the compiled runtime lock.')
        try:
            manifest = json.loads(raw)
            members = manifest['members']
            if (manifest['schema'] != FLASH_MANIFEST_SCHEMA or manifest['authority_version'] != FLASH_AUTHORITY_VERSION
                    or not isinstance(members, list) or not 1 <= len(members) <= 32
                    or len({row['path'] for row in members}) != len(members)):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise LaneError('SESSION_FLASH_MANIFEST_INVALID', 'The locked Flash manifest is malformed.') from None
        if registry is not None and action_set_digest(registry) != manifest['action_set_digest']:
            raise LaneError('SESSION_FLASH_REGISTRY_CHANGED', 'Recompile Flash against this exact executable registry.')
        loaded = {}
        for member in members:
            content = self._read(member['path'], 16 * 1024 * 1024)
            if len(content) != member['bytes'] or hashlib.sha256(content).hexdigest() != member['sha256']:
                raise LaneError('SESSION_FLASH_MEMBER_HASH_MISMATCH', 'A locked Flash member differs from its compiled bytes.',
                                details={'member': member['path']})
            loaded[member['path']] = content
            if member['path'].endswith('.sqlite'):
                # Deserialize the already hashed exact bytes, avoiding a path reopen race.
                with sqlite3.connect(':memory:') as connection:
                    connection.deserialize(content)
                    connection.execute('PRAGMA query_only=ON')
                    connection.execute('PRAGMA trusted_schema=OFF')
                    if connection.execute('PRAGMA quick_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchone():
                        raise LaneError('SESSION_FLASH_SQLITE_INVALID', 'A locked routing database failed integrity verification.')
        try:
            context = loaded['env/UNIVERSAL_FLASH_PROMPT.md'].decode('utf-8')
        except (KeyError, UnicodeError):
            raise LaneError('SESSION_FLASH_CONTEXT_MISSING', 'The verified Flash context is unavailable.') from None
        return FlashStatus(manifest_digest=FLASH_MANIFEST_SHA256,
            action_set_digest=manifest['action_set_digest'], member_count=len(members), members=members, context=context)

    def policy_rows(self, role, tables, *, registry=None, max_rows=10000):
        """Read bounded typed rows from exact verified package bytes only."""
        if role not in {'env', 'uop'} or not 1 <= len(tables) <= 64 or not 1 <= max_rows <= 20000:
            raise LaneError('ENV_UOP_READ_BUDGET', 'Select a bounded set of ENV or UOP policy tables.')
        if len(set(tables)) != len(tables) or any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value) for value in tables):
            raise LaneError('ENV_UOP_TABLE_INVALID', 'Select exact distinct policy table identifiers.')
        status = self.verify(registry=registry)
        name = f'{role}/{role}_sqlite.sqlite'
        member = next((item for item in status.members if item['path'] == name), None)
        if member is None:
            raise LaneError('ENV_UOP_DATABASE_MISSING', 'The full operating policy database is absent from Flash.')
        raw = self._read(name, 16 * 1024 * 1024)
        if len(raw) != member['bytes'] or hashlib.sha256(raw).hexdigest() != member['sha256']:
            raise LaneError('SESSION_FLASH_MEMBER_HASH_MISMATCH', 'The policy database changed before its read.')
        result = {}; remaining = max_rows
        with sqlite3.connect(':memory:') as connection:
            connection.deserialize(raw)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA trusted_schema=OFF')
            available = {row[0] for row in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
            for table in tables:
                if table not in available:
                    raise LaneError('ENV_UOP_TABLE_MISSING', 'A required current operating table is missing.', details={'table': table})
                rows = connection.execute('SELECT * FROM "' + table + '" LIMIT ?', (remaining + 1,)).fetchall()
                if len(rows) > remaining:
                    raise LaneError('ENV_UOP_READ_BUDGET', 'The selected operating policy exceeds the row budget.')
                remaining -= len(rows)
                result[table] = [dict(row) for row in rows]
        return {'manifest_digest': status.manifest_digest, 'database_sha256': member['sha256'], 'tables': result,
                'rows_read': max_rows - remaining, 'project_payload_accessed': False}

    def query_policy(self, role, query, limit=8, *, registry=None):
        """Traverse the owning generated LlamaIndex/FTS policy index read-only."""
        from .sqlite_indexing import query_authority_index
        if role not in {'env', 'uop'} or not isinstance(query, str) or not 1 <= len(query) <= 240 or type(limit) is not int or not 1 <= limit <= 20:
            raise LaneError('ENV_UOP_READ_BUDGET', 'Use a bounded policy query and result count.')
        tokens = re.findall(r'[^\W_]+', query, flags=re.UNICODE)
        if not tokens or len(tokens) > 12:
            raise LaneError('ENV_UOP_QUERY_INVALID', 'Use one to twelve policy search terms.')
        match = ' AND '.join('"' + token.replace('"', '""') + '"' for token in tokens)
        status = self.verify(registry=registry)
        name = f'{role}/{role}_sqlite.sqlite'
        member = next(item for item in status.members if item['path'] == name)
        raw = self._read(name, 16 * 1024 * 1024)
        if hashlib.sha256(raw).hexdigest() != member['sha256'] or len(raw) != member['bytes']:
            raise LaneError('SESSION_FLASH_MEMBER_HASH_MISMATCH', 'The policy changed before bounded retrieval.')
        with sqlite3.connect(':memory:') as connection:
            connection.deserialize(raw)
            connection.execute('PRAGMA query_only=ON')
            connection.execute('PRAGMA trusted_schema=OFF')
            matches = query_authority_index(connection, authority_id=role, match=match, limit=limit)
        if len(json_text(matches).encode()) > 131072:
            raise LaneError('ENV_UOP_READ_BUDGET', 'Narrow this policy query to fit its output byte budget.')
        return {'manifest_digest': status.manifest_digest, 'matches': matches, 'project_payload_accessed': False}

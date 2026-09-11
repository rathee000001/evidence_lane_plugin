"""Internal, standard-library-only recovery of a parent's disposable SQLite copy."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import struct
import sys
import time
from pathlib import Path

JOURNAL_MAGIC = bytes.fromhex('d9d505f920a163d7')
SQLITE_MAGIC = b'SQLite format 3\x00'


def validate_journal(main: bytes, journal: bytes, max_bytes: int) -> dict[str, int]:
    if (type(max_bytes) is not int or not 1 <= max_bytes <= 768 * 1024 * 1024
            or len(main) < 100 or len(main) + len(journal) > max_bytes):
        raise ValueError('SQLITE_RECOVERY_BYTE_BUDGET')
    if len(journal) < 28 or journal[:8] != JOURNAL_MAGIC:
        raise ValueError('SQLITE_RECOVERY_JOURNAL_HEADER_INVALID')
    # SQLite can consult/delete a named super-journal during playback. Never
    # follow a filename from this source through a writable recovery connection.
    if len(journal) >= 16 and journal[-8:] == JOURNAL_MAGIC:
        raise ValueError('SQLITE_RECOVERY_EXTERNAL_JOURNAL_REFERENCE')
    position = records = segments = 0
    original_pages = page_size = sector_size = 0
    deadline = time.monotonic() + 10
    while position + 28 <= len(journal):
        if time.monotonic() >= deadline:
            raise ValueError('SQLITE_RECOVERY_TIME_BUDGET')
        if journal[position:position + 8] != JOURNAL_MAGIC:
            # An unsynced final segment has a zero header and is not replayed.
            break
        _, count, nonce, original, sector, page = struct.unpack_from('>8s5I', journal, position)
        if (not 512 <= page <= 65536 or page & (page - 1)
                or not 512 <= sector <= 65536 or sector & (sector - 1)
                or not 1 <= original <= max_bytes // page):
            raise ValueError('SQLITE_RECOVERY_JOURNAL_BOUNDS')
        if segments and (original, sector, page) != (original_pages, sector_size, page_size):
            raise ValueError('SQLITE_RECOVERY_JOURNAL_SEGMENT_MISMATCH')
        original_pages, sector_size, page_size = original, sector, page
        segments += 1
        position += sector
        if position > len(journal):
            raise ValueError('SQLITE_RECOVERY_JOURNAL_TRUNCATED')
        if count == 0xFFFFFFFF:
            count = (len(journal) - position) // (page + 8)
        if count > (len(journal) - position) // (page + 8):
            raise ValueError('SQLITE_RECOVERY_JOURNAL_TRUNCATED')
        for _ in range(count):
            if time.monotonic() >= deadline:
                raise ValueError('SQLITE_RECOVERY_TIME_BUDGET')
            page_number = struct.unpack_from('>I', journal, position)[0]
            if not 1 <= page_number <= original_pages:
                raise ValueError('SQLITE_RECOVERY_JOURNAL_PAGE_BUDGET')
            payload = journal[position + 4:position + 4 + page]
            checksum = (nonce + sum(payload[page - 200::-200])) & 0xFFFFFFFF
            if checksum != struct.unpack_from('>I', journal, position + 4 + page)[0]:
                raise ValueError('SQLITE_RECOVERY_JOURNAL_CHECKSUM')
            position += page + 8
            records += 1
        position = ((position + sector - 1) // sector) * sector
    return {'journal_segments': segments, 'journal_pages': records,
        'original_database_pages': original_pages, 'page_size': page_size,
        'maximum_database_bytes': original_pages * page_size}


def _read_private(path: Path, limit: int) -> bytes:
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or path.is_symlink()
            or getattr(metadata, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
            or metadata.st_size > limit):
        raise ValueError('SQLITE_RECOVERY_PRIVATE_FILE_REQUIRED')
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) != metadata.st_size or len(content) > limit:
        raise ValueError('SQLITE_RECOVERY_PRIVATE_FILE_CHANGED')
    return content


def recover(request: dict) -> dict:
    if not isinstance(request, dict) or set(request) != {'main_sha256', 'journal_sha256', 'max_bytes'}:
        raise ValueError('SQLITE_RECOVERY_CONTRACT_INVALID')
    limit = request['max_bytes']
    if type(limit) is not int or not 1 <= limit <= 768 * 1024 * 1024:
        raise ValueError('SQLITE_RECOVERY_BYTE_BUDGET')
    path = Path.cwd() / 'inspection.sqlite'
    journal_path = path.with_name(path.name + '-journal')
    if path.with_name(path.name + '-wal').exists():
        raise ValueError('SQLITE_RECOVERY_CONFLICTING_JOURNALS')
    main = _read_private(path, limit)
    journal = _read_private(journal_path, limit - len(main))
    if (hashlib.sha256(main).hexdigest() != request['main_sha256']
            or hashlib.sha256(journal).hexdigest() != request['journal_sha256']):
        raise ValueError('SQLITE_RECOVERY_PRIVATE_FILE_CHANGED')
    bounds = validate_journal(main, journal, limit)
    del main, journal
    deadline, steps = time.monotonic() + 10, 0
    def progress():
        nonlocal steps
        steps += 1
        return int(steps >= 20_000_000 or (steps % 1000 == 0 and time.monotonic() >= deadline))
    connection = sqlite3.connect(path.as_uri() + '?mode=rw', uri=True, timeout=0.25)
    try:
        connection.enable_load_extension(False)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16 * 1024 * 1024)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1024 * 1024)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 256)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        if hasattr(connection, 'setconfig'):
            connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
        connection.set_progress_handler(progress, 1)
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        if connection.execute('PRAGMA quick_check(1)').fetchone()[0] != 'ok':
            raise ValueError('SQLITE_RECOVERY_INTEGRITY_FAILED')
    finally:
        connection.close()
    content = _read_private(path, limit)
    if not content.startswith(SQLITE_MAGIC) or len(content) > bounds['maximum_database_bytes']:
        raise ValueError('SQLITE_RECOVERY_IMAGE_INVALID')
    if journal_path.exists() and journal_path.stat().st_size:
        with journal_path.open('rb') as stream:
            if stream.read(1) != b'\x00':
                raise ValueError('SQLITE_RECOVERY_JOURNAL_REMAINS_HOT')
    return {'status': 'ROLLED_BACK_PRIVATE_COPY', 'sqlite_version': sqlite3.sqlite_version,
        'recovered_sha256': hashlib.sha256(content).hexdigest(), 'recovered_bytes': len(content),
        'source_paths_received': False, 'external_journal_reference_followed': False,
        'vm_step_limit': 20_000_000, 'worker_timeout_seconds': 15, **bounds}


def main() -> None:
    try:
        if len(sys.argv) != 2 or len(sys.argv[1]) > 8192:
            raise ValueError('SQLITE_RECOVERY_CONTRACT_INVALID')
        result = recover(json.loads(sys.argv[1]))
    except (ValueError, OSError, sqlite3.DatabaseError) as error:
        code = str(error) if isinstance(error, ValueError) else 'SQLITE_RECOVERY_FAILED'
        print(json.dumps({'status': 'FAILED', 'error_code': code}))
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == '__main__':
    main()

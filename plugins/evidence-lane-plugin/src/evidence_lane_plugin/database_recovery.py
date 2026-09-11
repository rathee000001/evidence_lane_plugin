"""Consistent project backups and offline recovery into a fresh state root.

Backups pin project evidence head coordinator and preserve the exact separate SQLite database bytes that
its lane heads identify, with all registered content, schema and view history.
Runtime credentials, locks, caches and unregistered files are not restored.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from contextlib import nullcontext
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from .bounded_io import IOBudget, bounded_file_identity
from .errors import LaneError
from .locking import RuntimeLock
from .migrations import Migration, apply_migrations
from .plan_runtime import content_digest
from .projects import ProjectDirectory, atomic_json
from .registry import ActionSpec, Contract
from .sdk import UUID_PATTERN
from .storage import (
    APPLICATION_ID,
    DATABASE_NAME,
    FORMAT_VERSION,
    STORAGE_LAYOUT,
    LaneStore,
    ProjectStore,
    bounded_project_read,
    json_text,
    now,
    reject_links,
)
from .store import DIGEST, SourceRestoration, assert_quiescent, has_table, recovery_operation

MAX_FILES = 50000
MAX_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST = 32 * 1024 * 1024
RECOVERY_MIGRATIONS = (Migration('recovery',1,'Backup receipts and explicit offline database recovery boundaries',(
    """CREATE TABLE recovery_backups (request_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
       input_digest TEXT NOT NULL, result_json TEXT NOT NULL CHECK(json_valid(result_json)))""",
    """CREATE TABLE recovery_history (sequence INTEGER PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
       body_json TEXT NOT NULL CHECK(json_valid(body_json)))""",
    """CREATE TABLE recovery_control (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
       recovery_digest TEXT NOT NULL, required_after_revision INTEGER NOT NULL, cleared_by_revision INTEGER)""",
)),)


class BackupRequest(Contract):
    request_id: str = Field(default_factory=lambda:str(uuid4()),pattern=UUID_PATTERN)
    destination_root: str = Field(min_length=1,max_length=1000)


class BackupResult(Contract):
    project_id: str
    request_id: str
    backup_root: str
    manifest_digest: str
    database_digest: str
    file_count: int
    total_bytes: int
    created_at: str
    scope: str = 'coherent_root_pv_separate_lane_databases_registered_files_and_schema_view_history'
    lane_count: int = 0
    root_pv: dict = Field(default_factory=dict)
    live_source_workspace_copied: bool = False
    runtime_credentials_included: bool = False


class BackupVerify(Contract):
    backup_root: str = Field(min_length=1,max_length=1000)
    manifest_digest: str = Field(pattern=DIGEST)


class BackupVerified(Contract):
    project_id: str
    manifest_digest: str
    file_count: int
    total_bytes: int
    sqlite_integrity: str = 'ok'
    project_mutated: bool = False
    lane_count: int = 0
    root_pv: dict = Field(default_factory=dict)


class RecoveryInspect(Contract):
    sample_limit: int = Field(default=20,ge=0,le=50)


class RecoveryInspection(Contract):
    project_id: str
    registered_file_count: int
    registered_bytes: int
    unregistered_file_count: int
    unregistered_sample: list[str]
    database_recovery: dict | None
    repairs_performed: bool = False


def require_recovery_plan(connection, supplied_digest, revision):
    row = connection.execute('SELECT * FROM recovery_control WHERE singleton=1').fetchone() if has_table(connection,'recovery_control') else None
    if row and row['cleared_by_revision'] is None:
        if supplied_digest != row['recovery_digest'] or revision <= row['required_after_revision']:
            raise LaneError('DATABASE_RECOVERY_PLAN_REQUIRED','Bind the new Plan to the exact administrative database recovery digest.')
        connection.execute('UPDATE recovery_control SET cleared_by_revision=? WHERE singleton=1',(revision,))
    elif supplied_digest is not None:
        raise LaneError('DATABASE_RECOVERY_PLAN_MISMATCH','There is no pending database recovery for this Plan change.')


def require_recovery_execution_ready(connection):
    if has_table(connection,'recovery_control') and connection.execute(
            'SELECT 1 FROM recovery_control WHERE cleared_by_revision IS NULL').fetchone():
        raise LaneError('DATABASE_RECOVERY_PLAN_REQUIRED','Refresh and verify the Plan before executing recovered project work.')


def _relative(value):
    from .recovery_snapshot import relative_file
    return relative_file(value)


def _hash(path,root,*,budget=None):
    item=bounded_file_identity(path,root=root,budget=budget or IOBudget(max_file_bytes=256*1024*1024))
    return {'sha256':item['sha256'].lower(),'bytes':item['size_bytes']}


def _copy(source, source_root, target, target_root, expected, *, tick=None):
    reject_links(source,source_root)
    reject_links(target,target_root)
    target.parent.mkdir(parents=True,exist_ok=True)
    reject_links(target,target_root)
    before=source.stat()
    if before.st_size != expected['bytes'] or before.st_size > 256*1024*1024:
        raise LaneError('RECOVERY_FILE_CHANGED','A file differs from its recorded size.')
    digest=hashlib.sha256()
    count=0
    descriptor=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(descriptor,'wb') as output,source.open('rb') as incoming:
        while block:=incoming.read(1024*1024):
            count+=len(block)
            if count>expected['bytes']:
                raise LaneError('RECOVERY_FILE_CHANGED','A file grew during copying.')
            if tick:
                tick()
            digest.update(block)
            output.write(block)
        output.flush()
        os.fsync(output.fileno())
    after=source.stat()
    if (count!=expected['bytes'] or digest.hexdigest()!=expected['sha256']
            or any(getattr(before,key)!=getattr(after,key) for key in ('st_size','st_mtime_ns','st_ino','st_dev'))):
        raise LaneError('RECOVERY_FILE_CHANGED','A file changed during copying; its incomplete output remains unselected.')


def _write_new(path,content):
    descriptor=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(descriptor,'wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _read_json(path,root,limit):
    reject_links(path,root)
    if path.stat().st_size>limit:
        raise LaneError('RECOVERY_DOCUMENT_BUDGET','The selected recovery document exceeds its size bound.')
    with path.open('rb') as stream:
        content=stream.read(limit+1)
    if len(content)>limit:
        raise LaneError('RECOVERY_DOCUMENT_BUDGET','The recovery document grew beyond its size bound.')
    return json.loads(content.decode('utf-8'))


def _sqlite(path):
    reject_links(path,Path(path.anchor))
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,isolation_level=None,timeout=5)
    connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA trusted_schema=OFF')
    connection.execute('PRAGMA query_only=ON')
    deadline=time.monotonic()+30
    connection.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
    return connection


def _integrity(connection,project_id,*,lane=None):
    deadline=time.monotonic()+30
    connection.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
    if connection.execute('PRAGMA page_count').fetchone()[0]*connection.execute('PRAGMA page_size').fetchone()[0]>256*1024*1024:
        raise LaneError('BACKUP_DATABASE_BUDGET','The project exceeds the 256 MiB administrative recovery budget.')
    if lane is None:
        if connection.execute('PRAGMA application_id').fetchone()[0]!=APPLICATION_ID:
            raise LaneError('BACKUP_PROJECT_INVALID','The backup database is not a v4 project evidence head coordinator.')
        row=connection.execute('SELECT * FROM project WHERE singleton=1').fetchone()
        if not row or row['project_id']!=project_id or row['format_version']!=FORMAT_VERSION:
            raise LaneError('BACKUP_PROJECT_MISMATCH','The backup does not belong to the selected project and format.')
    else:
        from .recovery_snapshot import lane_identity
        row = lane_identity(connection, project_id, lane)
    if [r[0] for r in connection.execute('PRAGMA integrity_check')]!=['ok'] or connection.execute('PRAGMA foreign_key_check').fetchone():
        raise LaneError('BACKUP_SQLITE_INTEGRITY','The backup failed SQLite or foreign-key integrity checks.')
    if connection.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger'").fetchone():
        raise LaneError('BACKUP_SCHEMA_UNSUPPORTED','Project recovery does not execute database triggers.')
    return dict(row)


def _inventory(project, *, tick=lambda: None, require_quiescent=True):
    from .recovery_snapshot import inventory
    return inventory(project, tick=tick, require_quiescent=require_quiescent)


def verify_backup(root,expected_digest,project_id,*,tick=None):
    return recovery_operation(lambda:_verify_backup(root,expected_digest,project_id,tick=tick))


def _verify_backup(root,expected_digest,project_id,*,tick=None):
    root=Path(os.path.abspath(Path(root).expanduser()))
    reject_links(root,Path(root.anchor))
    manifest=root/'backup.json'
    reject_links(manifest,root)
    if not re.fullmatch(DIGEST,expected_digest) or manifest.stat().st_size>MAX_MANIFEST:
        raise LaneError('BACKUP_MANIFEST_INVALID','Select a bounded backup manifest and exact digest.')
    body=_read_json(manifest,root,MAX_MANIFEST)
    if content_digest(body)!=expected_digest or body.get('schema')!='evidence-lane.database-backup.v4' or body.get('project_id')!=project_id or body.get('storage_layout')!=STORAGE_LAYOUT:
        raise LaneError('BACKUP_MANIFEST_MISMATCH','The backup manifest differs from the selected project and digest.')
    if not isinstance(body.get('files'),list) or not 1<=len(body['files'])<=MAX_FILES+1:
        raise LaneError('BACKUP_CONTENT_BUDGET','Select a bounded backup file manifest.')
    selected={}
    budget=IOBudget(max_file_bytes=256*1024*1024,max_file_count=MAX_FILES+1,max_aggregate_bytes=MAX_BYTES)
    for item in body['files']:
        relative=_relative(item['path'])
        if item['path'] in selected:
            raise LaneError('BACKUP_FILE_COLLISION','The backup repeats a file identity.')
        if tick:
            tick()
        actual=_hash(root/'payload'/relative,root,budget=budget)
        if actual!={key:item[key] for key in ('sha256','bytes')}:
            raise LaneError('BACKUP_FILE_CHANGED','A backup file differs from its recorded digest or size.')
        selected[item['path']]=item
    if DATABASE_NAME not in selected:
        raise LaneError('BACKUP_DATABASE_MISSING','The backup does not contain project SQLite.')
    store = ProjectStore(root/'payload', read_only=True)
    with bounded_project_read(store.root, time.monotonic()+30):
        # This read belongs to the backup root. A caller's live writer heartbeat
        # must not run while a different project's snapshot is pinned.
        actual = _inventory(store)
        if actual['source_root'] != body['source_root']:
            raise LaneError('BACKUP_SOURCE_MISMATCH', 'The backup source locator differs from project evidence head coordinator.')
        if (actual['files'] != sorted(selected.values(), key=lambda item:item['path'])
                or actual['root_pv'] != body.get('root_pv') or actual['lanes'] != body.get('lanes')):
            raise LaneError('BACKUP_INVENTORY_MISMATCH', 'The backup differs from its exact lane registries and project evidence head coordinator.')
        verify_authority_history(store)

    return body


def verify_authority_history(store):
    """Run retained owner verifiers in place, without migrations or repair."""
    from .agent_learning import LearningStore
    from .canon_task_graph import CanonStore
    from .capture_routing import CaptureRouteAuthority
    from .lineage import ChatLineage
    from .plan_runtime import PlanStore
    from .project_memory import ProjectMemory
    from .project_universe import ProjectLinksVerify, ProjectUniverse
    from .session_authority import SessionAuthority
    from .store import validate_source_locator
    from .task_binding_registry import TaskContinuity
    from .universe_federation import FederationVerify, UniverseFederation
    owners = (
        ('plan', 'plan_current', lambda: PlanStore(store).verify_history()),
        ('chat_lineage', 'lineage_events', lambda: ChatLineage(store).verify()),
        ('receipts', 'capture_decisions', lambda: CaptureRouteAuthority(store).verify()),
        ('memory', 'memory_events', lambda: ProjectMemory(store).verify_history()),
        ('learning', 'learning_events', lambda: LearningStore(store).verify_history()),
        ('canon', 'canon_events', lambda: CanonStore(store).verify_history()),
        ('chat_lineage', 'continuation_events', lambda: TaskContinuity(store).verify_history()),
        ('receipts', 'sessions_events', lambda: SessionAuthority(store).verify_history()),
        ('universe', 'universe_link_events', lambda: ProjectUniverse(store).verify(ProjectLinksVerify(max_events=10000))),
        ('universe', 'federation_identity', lambda: UniverseFederation(store).verify(FederationVerify(max_records=10000))),
    )
    with bounded_project_read(store.root, time.monotonic()+30):
        for lane_id, table, verify in owners:
            with store.lane(lane_id).connection(read_only=True) as connection:
                initialized = has_table(connection, table) and connection.execute('SELECT 1 FROM '+table+' LIMIT 1').fetchone()
            if initialized:
                verify()
        with store.lane('sources').connection(read_only=True) as connection:
            restored = has_table(connection, 'restoration_history') and connection.execute('SELECT 1 FROM restoration_history LIMIT 1').fetchone()
        if restored:
            validate_source_locator(store, str(store.source_root))
        verify_recovery_history(store)


def verify_recovery_history(store):
    with store.lane('receipts').connection(read_only=True) as connection:
        if not has_table(connection, 'recovery_history'):
            return
        rows = connection.execute('SELECT * FROM recovery_history ORDER BY sequence LIMIT 10001').fetchall()
        if len(rows) > 10000:
            raise LaneError('RECOVERY_HISTORY_BUDGET', 'Recovery history exceeds the bounded verifier.')
        previous = None
        for index, row in enumerate(rows, 1):
            body = json.loads(row['body_json']) if len(row['body_json'].encode()) <= 65536 else {}
            if (row['sequence'] != index or body.get('sequence') != index or body.get('project_id') != store.project_id
                    or body.get('previous_digest') != previous or content_digest(body) != row['digest']):
                raise LaneError('RECOVERY_HISTORY_INTEGRITY', 'Recovery history differs from its exact hash chain.')
            previous = row['digest']
        control = connection.execute('SELECT * FROM recovery_control WHERE singleton=1').fetchone()
        if control and (not rows or control['recovery_digest'] != rows[-1]['digest']):
            raise LaneError('RECOVERY_HISTORY_INTEGRITY', 'Recovery control differs from the latest recovery record.')


class DatabaseRecovery:
    def __init__(self,engine,store):
        self.engine = engine
        self.store = store.project if isinstance(store, LaneStore) else store
        self.receipts = self.store.lane('receipts')

    def backup(self,request,lease,*,actor_id,authorize=None):
        request=BackupRequest.model_validate(request.model_dump())
        lease.check()
        apply_migrations(self.receipts,RECOVERY_MIGRATIONS,writer=lease)
        input_digest=content_digest(request.model_dump())
        with self.receipts.connection(read_only=True) as connection:
            prior=connection.execute('SELECT * FROM recovery_backups WHERE request_id=?',(request.request_id,)).fetchone()
        with self.store.lane('plan').connection(read_only=True) as connection:
            assert_quiescent(connection)
        if prior:
            if prior['input_digest']!=input_digest or prior['actor_id']!=actor_id:
                raise LaneError('BACKUP_REQUEST_CONFLICT','This request belongs to a different backup or client.')
            result=BackupResult.model_validate_json(prior['result_json'])
            verify_backup(result.backup_root,result.manifest_digest,self.store.project_id)
            return result
        destination=SourceRestoration(self.engine,self.store)._destination(request.destination_root,allow_existing=True)
        tick=SourceRestoration._heartbeat(lease)
        if destination.exists():
            # A complete sealed snapshot may precede a failed live receipt write.
            manifest=destination/'backup.json'
            if not manifest.is_file() or manifest.stat().st_size>MAX_MANIFEST:
                raise LaneError('BACKUP_INCOMPLETE','Preserve this incomplete output and select a fresh backup destination.')
            digest=content_digest(_read_json(manifest,destination,MAX_MANIFEST))
            body=verify_backup(destination,digest,self.store.project_id,tick=tick)
            if (body['request_id'],body['actor_id'],body['input_digest'])!=(request.request_id,actor_id,input_digest):
                raise LaneError('BACKUP_DESTINATION_OCCUPIED','This directory belongs to another backup.')
        else:
            destination.mkdir()
            payload=destination/'payload'
            payload.mkdir()
            _write_new(payload/'.backup-sealed',b'Immutable backup payload; restore through administrative recovery.\n')
            # Heartbeats mutate project evidence head coordinator, so extend the lease before pinning it;
            # inside the bounded snapshot only check ownership and the deadline.
            lease.heartbeat()
            deadline = time.monotonic()+30
            def snapshot_tick():
                if time.monotonic() >= deadline:
                    raise LaneError('BACKUP_TIMEOUT', 'The coherent backup exceeded its 30 second snapshot budget.')
                lease.check()
                if authorize:
                    authorize()
            with bounded_project_read(self.store.root, deadline):
                inventory = _inventory(self.store, tick=snapshot_tick)
                verify_authority_history(self.store)
                for item in inventory['files']:
                    _copy(self.store.root/_relative(item['path']), self.store.root,
                          payload/_relative(item['path']), payload, item, tick=snapshot_tick)
                body={'schema':'evidence-lane.database-backup.v4','project_id':self.store.project_id,
                      'request_id':request.request_id,'actor_id':actor_id,'input_digest':input_digest,
                      'state_root':str(self.store.root),'source_root':inventory['source_root'],
                      'created_at':now(),'files':inventory['files'], 'root_pv':inventory['root_pv'],
                      'lanes':inventory['lanes'], 'storage_layout':STORAGE_LAYOUT,
                      'live_source_workspace_copied':False,'runtime_credentials_included':False}
            encoded=json_text(body).encode()
            if len(encoded)>MAX_MANIFEST:
                raise LaneError('BACKUP_MANIFEST_BUDGET','The backup manifest exceeds its bounded size.')
            _write_new(destination/'backup.json',encoded)
            digest=content_digest(body)
            verify_backup(destination,digest,self.store.project_id,tick=tick)
        result=BackupResult(project_id=self.store.project_id,request_id=request.request_id,backup_root=str(destination),
            manifest_digest=digest,database_digest=next(item['sha256'] for item in body['files'] if item['path']==DATABASE_NAME),
            file_count=len(body['files']),total_bytes=sum(item['bytes'] for item in body['files']),created_at=body['created_at'],
            lane_count=len(body['lanes']),root_pv=body['root_pv'])
        if authorize:
            authorize()
        with lease.transaction('receipts') as connection:
            connection.execute('INSERT INTO recovery_backups VALUES(?,?,?,?)',(request.request_id,actor_id,input_digest,result.model_dump_json()))
            self.store.append_receipt('database_backup_verified',result.model_dump(),connection=connection)
        return result

    def inspect(self,request):
        with bounded_project_read(self.store.root, time.monotonic()+30):
            inventory = _inventory(self.store, require_quiescent=False)
            files = inventory['files']
            with self.receipts.connection(read_only=True) as connection:
                control=connection.execute('SELECT * FROM recovery_control WHERE singleton=1').fetchone() if has_table(connection,'recovery_control') else None
            budget=IOBudget(max_file_bytes=256*1024*1024,max_file_count=MAX_FILES,max_aggregate_bytes=MAX_BYTES)
            for item in files:
                if _hash(self.store.root/_relative(item['path']),self.store.root,budget=budget)!={key:item[key] for key in ('sha256','bytes')}:
                    raise LaneError('RECOVERY_FILE_CHANGED','A registered project file failed its integrity check.')
            verify_authority_history(self.store)
            registered={item['path'] for item in files}
            orphans=[]
            observed=0
            for base in ('authorities','sectors'):
                for folder,directories,names in os.walk(self.store.root/base,followlinks=False):
                    for name in [*directories,*names]:
                        reject_links(Path(folder)/name,self.store.root)
                    for name in names:
                        observed+=1
                        if observed>MAX_FILES:
                            raise LaneError('RECOVERY_SCAN_BUDGET','The project exceeds the bounded file inspection budget.')
                        relative=(Path(folder)/name).relative_to(self.store.root).as_posix()
                        if relative not in registered:
                            orphans.append(relative)
            return RecoveryInspection(project_id=self.store.project_id,registered_file_count=len(files),
                registered_bytes=sum(item['bytes'] for item in files),unregistered_file_count=len(orphans),
                unregistered_sample=sorted(orphans)[:request.sample_limit],database_recovery=dict(control) if control else None)



def restore_backup_offline(runtime_root, project_id, backup_root, manifest_digest, destination_root, *, reconcile=False):
    """Current-user administration. Never opens or overwrites the old database."""
    runtime_root=Path(os.path.abspath(Path(runtime_root).expanduser()))
    reject_links(runtime_root,Path(runtime_root.anchor))
    if not (runtime_root/'projects.json').is_file():
        raise LaneError('RECOVERY_DIRECTORY_REQUIRED','Select the existing engine project directory.')
    with RuntimeLock(runtime_root/'engine.lock'):
        directory=ProjectDirectory(runtime_root)
        entries=directory.entries()
        current=entries.get(project_id)
        if current is None:
            raise LaneError('PROJECT_NOT_REGISTERED','Select the exact registered project to recover.')
        original=Path(current['state_root'])
        destination=Path(destination_root).expanduser()
        if not destination.is_absolute() or '..' in destination.parts:
            raise LaneError('RECOVERY_ABSOLUTE_PATH_REQUIRED','Choose an absolute fresh recovery directory.')
        destination=Path(os.path.abspath(destination))
        reject_links(destination,Path(destination.anchor))
        roots=[runtime_root,Path(backup_root).resolve(strict=True)]
        for item in entries.values():
            roots.extend([Path(item['state_root']),Path(item['source_root'])])
        if (destination.exists() and not reconcile) or not destination.parent.is_dir() or any(
                destination.is_relative_to(root) or root.is_relative_to(destination) for root in roots):
            raise LaneError('RECOVERY_FRESH_ROOT_REQUIRED','Use an absent state root separate from project, source, runtime and backup directories.')
        with (RuntimeLock(original/'writer.lock') if original.is_dir() else nullcontext()):
            body=verify_backup(backup_root,manifest_digest,project_id)
            source=Path(body['source_root'])
            reject_links(source,Path(source.anchor))
            if not source.is_absolute() or not source.is_dir() or destination.is_relative_to(source) or source.is_relative_to(destination):
                raise LaneError('RECOVERY_SOURCE_REQUIRED','The backup source locator must still be an existing separate workspace; this action does not restore source files.')
            if reconcile:
                result=verify_unselected_recovery(destination,project_id,manifest_digest,original)
                entries[project_id]={**current,'state_root':str(destination),'source_root':result['source_root']}
                atomic_json(directory.path,{'version':1,'projects':entries})
                return result
            destination.mkdir()
            for item in body['files']:
                relative=_relative(item['path'])
                _copy(Path(backup_root)/'payload'/relative,Path(backup_root).resolve(),destination/relative,destination,item)
            store=ProjectStore(destination)
            from .lanes import get_lane
            for item in body['lanes']:
                definition = get_lane(item['lane_id'])
                for relative in (definition.files_relative_path, definition.schema_history_relative_path):
                    (destination/relative).mkdir(parents=True, exist_ok=True)
            with store.coordinated_transaction(['receipts', 'plan', 'universe']) as commit:
                # Schema readiness and every recovery mutation share one
                # publication; a failed receipt must not leave a partial head.
                apply_migrations(store,RECOVERY_MIGRATIONS)
                connection = commit.connection('receipts')
                plan = commit.connection('plan')
                root = commit.connection(None)
                revoked=connection.execute('UPDATE access_grants SET revoked_at=? WHERE revoked_at IS NULL',(now(),)).rowcount if has_table(connection,'access_grants') else 0
                if has_table(connection,'extensions_registration'):
                    from .connector_governance import ConnectorGovernance
                    extensions=connection.execute('SELECT plugin_id FROM extensions_registration WHERE revoked_at IS NULL LIMIT 257').fetchall()
                    if len(extensions)>256:
                        raise LaneError('RECOVERY_EXTENSION_BUDGET','The recovered project exceeds its bounded extension inventory.')
                    governance=ConnectorGovernance(store,lanes=set(),hosts=set(),actions=set(),max_plugins=256)
                    for extension in extensions:
                        connection.execute('UPDATE extensions_registration SET revoked_at=? WHERE plugin_id=?',(now(),extension[0]))
                        governance._event(connection,extension[0],'revoked','administrative-recovery',{'backup_digest':manifest_digest})
                else:
                    extensions=[]
                from .universe_federation import UniverseFederation
                federation_grants = UniverseFederation(store).revoke_recovered_grants(
                    commit.connection('universe'), manifest_digest)
                if has_table(root,'writer_lease'):
                    root.execute('UPDATE writer_lease SET fence=fence+1,owner_id=NULL,engine_id=NULL,expires_at=NULL')
                if has_table(plan,'jobs_jobs'):
                    plan.execute("UPDATE jobs_jobs SET resumable=0,state='superseded',updated_at=? WHERE state='checkpointed'",(now(),))
                closed = close_recovery_session(store, connection, manifest_digest)
                revision=plan.execute('SELECT revision FROM plan_current').fetchone() if has_table(plan,'plan_current') else None
                previous=connection.execute('SELECT sequence,digest FROM recovery_history ORDER BY sequence DESC LIMIT 1').fetchone()
                record={'project_id':project_id,'original_state_root':str(original),'state_root':str(destination),
                        'source_root':str(source),'backup_digest':manifest_digest,'backup_state_root':body['state_root'],'created_at':now(),
                        'backup_root_pv':body['root_pv'], 'restored_lane_count':len(body['lanes']),
                        'previous_digest':previous['digest'] if previous else None,'sequence':previous['sequence']+1 if previous else 1,
                        'prior_grants_revoked':revoked,'extensions_revoked':len(extensions),'sessions_closed':closed,
                        'federation_grants_revoked':federation_grants,
                        'source_files_restored':False,'source_bytes_reverified':False,
                        'automatic_job_replay':False,'native_task_attestation':'not_provided'}
                recovery_digest=content_digest(record)
                connection.execute('INSERT INTO recovery_history VALUES(?,?,?)',(record['sequence'],recovery_digest,json_text(record)))
                connection.execute('INSERT OR REPLACE INTO recovery_control VALUES(1,?,?,NULL)',(recovery_digest,revision[0] if revision else 0))
                store.append_receipt('database_recovered_to_fresh_root',{**record,'recovery_digest':recovery_digest},connection=connection)
            with bounded_project_read(store.root, time.monotonic()+30):
                snapshot = _inventory(store)
                verify_authority_history(store)
            result={**record,'recovery_digest':recovery_digest,'plan_refresh_required':True,
                    'original_state_preserved':True,'clients_must_reconnect':True,
                    'recovered_snapshot_digest':content_digest(snapshot), 'root_pv':snapshot['root_pv'],
                    'recovered_database_digest':_hash(store.database,store.root)['sha256']}
            _write_new(destination/'recovery-result.json',json_text(result).encode())
            entries[project_id]={**current,'state_root':str(destination),'source_root':str(source)}
            atomic_json(directory.path,{'version':1,'projects':entries})
            return result


def close_recovery_session(store, connection, recovery_digest, *, reason='administrative_recovery'):
    from .session_authority import SessionAuthority
    owner = SessionAuthority(store)
    current = owner.current(connection)
    if current is None or current['state'] != 'active':
        return 0
    event = connection.execute('SELECT body_json FROM sessions_events WHERE digest=?', (current['event_digest'],)).fetchone()
    body = {**json.loads(event[0]), 'state':'closed', 'generation':current['generation']+1,
            'owner_authenticated':False, 'capture_bound':False, 'flash_current':False, 'operation':'recovery',
            'observation_scope':'at_transition_commit', 'exit_reason':reason,
            'root_pv':store.pv_head()}
    owner.append(connection, action='session_recovery_closed', client_id='administrative-recovery',
                 request_id=str(uuid4()), input_digest=recovery_digest, body=body)
    return 1


def verify_unselected_recovery(destination,project_id,backup_digest,original):
    """Finish a failed directory-hint update without recopying or rewriting data."""
    path=destination/'recovery-result.json'
    reject_links(path,destination)
    if not path.is_file() or path.stat().st_size>65536:
        raise LaneError('RECOVERY_RECONCILIATION_INCOMPLETE','The fresh root lacks a complete recovery result; preserve it and select another destination.')
    result=_read_json(path,destination,65536)
    record={key:value for key,value in result.items() if key not in {
        'recovery_digest','plan_refresh_required','original_state_preserved','clients_must_reconnect',
        'recovered_database_digest','recovered_snapshot_digest','root_pv'}}
    if (content_digest(record)!=result.get('recovery_digest') or record.get('project_id')!=project_id
            or record.get('backup_digest')!=backup_digest or record.get('state_root')!=str(destination)
            or record.get('original_state_root')!=str(original)):
        raise LaneError('RECOVERY_RECONCILIATION_MISMATCH','The unselected root differs from the exact recovery record.')
    store=ProjectStore(destination,read_only=True)
    with RuntimeLock(destination/'writer.lock'), bounded_project_read(store.root, time.monotonic()+30):
        snapshot = _inventory(store)
        if (content_digest(snapshot) != result.get('recovered_snapshot_digest')
                or snapshot['root_pv'] != result.get('root_pv')
                or _hash(store.database,store.root)['sha256'] != result.get('recovered_database_digest')):
            raise LaneError('RECOVERY_RECONCILIATION_CHANGED','The recovered root or lane databases changed after verification.')
        with store.lane('receipts').connection(read_only=True) as connection:
            row=connection.execute('SELECT * FROM recovery_history ORDER BY sequence DESC LIMIT 1').fetchone()
            if not row or row['digest']!=result['recovery_digest'] or json.loads(row['body_json'])!=record:
                raise LaneError('RECOVERY_RECONCILIATION_MISMATCH','The recovery result differs from project history.')
            if has_table(connection,'access_grants') and connection.execute('SELECT 1 FROM access_grants WHERE revoked_at IS NULL').fetchone():
                raise LaneError('RECOVERY_RECONCILIATION_LIVE','This root has already acquired new grants; inspect it independently.')
        for item in snapshot['files']:
            if _hash(destination/_relative(item['path']),destination)!={key:item[key] for key in ('sha256','bytes')}:
                raise LaneError('RECOVERY_FILE_CHANGED','A recovered file differs from its recorded identity.')
        verify_authority_history(store)
    return result


def register_recovery_actions(engine):
    def backup(context,request):
        store=engine.directory.open(context.project_id,write=True)
        with engine.project_work.mutation(store) as lease:
            return recovery_operation(lambda:DatabaseRecovery(engine,store).backup(request,lease,actor_id=context.client_id,
                authorize=(lambda: context.authorize('admin')) if context.authorize else None))
    def verify(context,request):
        body=verify_backup(request.backup_root,request.manifest_digest,context.project_id)
        return BackupVerified(project_id=context.project_id,manifest_digest=request.manifest_digest,
            file_count=len(body['files']),total_bytes=sum(item['bytes'] for item in body['files']),
            lane_count=len(body['lanes']),root_pv=body['root_pv'])
    engine.registry.register(ActionSpec('project_backup','Create and verify a consistent project database and registered-file backup.',
        BackupRequest,BackupResult,backup,permission='admin',mutates=True,profile='recovery', workflow='recover-project-state'))
    engine.registry.register(ActionSpec('project_backup_verify','Hash-check an exact project backup without restoring it.',
        BackupVerify,BackupVerified,verify,permission='admin',profile='recovery', workflow='recover-project-state'))
    engine.registry.register(ActionSpec('project_recovery_inspect','Inspect registered files, unregistered leftovers and database recovery status without repair.',
        RecoveryInspect,RecoveryInspection,
        lambda context,request:recovery_operation(lambda:DatabaseRecovery(engine,engine.directory.open(context.project_id)).inspect(request)),
        profile='recovery',studio_read=True, workflow='recover-project-state'))


def recovery_main():
    parser=argparse.ArgumentParser(description='Offline administrative project recovery; preserves the original state root.')
    parser.add_argument('--runtime-root',required=True)
    parser.add_argument('--project-id',required=True)
    parser.add_argument('--backup-root',required=True)
    parser.add_argument('--manifest-digest',required=True)
    parser.add_argument('--destination-root',required=True)
    parser.add_argument('--reconcile',action='store_true',help='Finish only a complete unselected recovery after a directory update failure.')
    args=parser.parse_args()
    try:
        result=restore_backup_offline(args.runtime_root,args.project_id,args.backup_root,args.manifest_digest,args.destination_root,reconcile=args.reconcile)
    except (LaneError,OSError,sqlite3.DatabaseError,ValueError,KeyError) as error:
        print(json_text({'status':'error','error':error.public() if isinstance(error,LaneError) else
                        {'code':'DATABASE_RECOVERY_FAILED','message':'Recovery failed; the existing project remains selected unless its final directory update committed.'}}))
        return 1
    print(json_text({'status':'ok','result':result}))
    return 0


if __name__=='__main__':
    raise SystemExit(recovery_main())

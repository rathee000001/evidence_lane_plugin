"""Code lane execution through the real registry, Delta writer and OS worker."""
from __future__ import annotations

import hashlib
import json
import subprocess
import time

import pytest
from evidence_lane_plugin.code_workers import code_worker_operations
from evidence_lane_plugin.connections import ConnectRequest, ProjectSelection
from evidence_lane_plugin.engine import Engine
from evidence_lane_plugin.errors import LaneError
from evidence_lane_plugin.internal_sdk import PublicActionSDKDispatcher
from evidence_lane_plugin.plan_runtime import PlanCreate, PlanStore, TaskDefinition
from evidence_lane_plugin.sdk import ActionRequest
from evidence_lane_plugin.workers import WorkerOperation, WorkerPool


@pytest.fixture
def code_system(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'helper.py').write_text('def greeting(name):\n    return "hello " + name\n', encoding='utf-8')
    (source / 'app.py').write_text('from helper import greeting\n\ndef run():\n    return greeting("world")\n', encoding='utf-8')
    (source / 'package.json').write_text('{"dependencies":{"example-lib":"^1.2.3"}}', encoding='utf-8')
    operations = (*code_worker_operations(), WorkerOperation('render_lane_view',
        'evidence_lane_plugin.artifact_contract', 'render_lane_view_worker', dependencies=('langgraph', 'langchain_core', 'graphviz')))
    with Engine(tmp_path / 'runtime', worker_pool=WorkerPool(operations, workers=1)) as engine:
        entry = engine.directory.register(tmp_path / 'state', source_root=source, create=True, read_only=False)
        store = engine.directory.open(entry['project_id'], write=True)
        _, session = engine.clients.connect(ConnectRequest(projects=[ProjectSelection(
            project_id=store.project_id, permissions=['read', 'write', 'tools', 'admin'])]))
        yield engine, store, session


def call(system, action, arguments=None, **kwargs):
    engine, store, session = system
    return PublicActionSDKDispatcher(engine).execute(ActionRequest(action=action, project_id=store.project_id,
        arguments=arguments or {}, **kwargs), session)


def create_plan(system, actions=('code_index',), **task_options):
    engine, store, _ = system
    tasks = [TaskDefinition(task_id='code-' + str(i), title=action, requested_outcome='Verify the selected Code operation',
        profile='code', allowed_actions=[action], permitted_tools=['Python', 'SQLite_FTS5_BM25', 'Python_structural_parser', 'Git',
            'TreeSitter_LanguagePack', 'SentenceTransformers', 'sqlite_vec',
            'LangGraph_Mermaid_engine', 'Python_Graphviz_DOT_engine', 'rustworkx'],
        permitted_paths=['.'], acceptance_checks=list(engine.registry.get(action).verification_checks), **task_options)
        for i, action in enumerate(actions)]
    with engine.project_work.mutation(store) as lease:
        PlanStore(store).create(PlanCreate(title='Code fixture', tasks=tasks), lease, actor_id='fixture')


def execute(system, action='code_index', arguments=None, index=0, timeout=25):
    store = system[1]
    task = PlanStore(store).task('code-' + str(index), expected_revision=1)
    admitted = call(system, 'delta_enter', {'task_id': task.definition.task_id, 'plan_revision': 1,
        'contract_digest': task.contract_digest, 'action': action, 'arguments': arguments or {'paths': ['.']}}, expected_revision=1)
    assert admitted.status == 'queued', admitted.error
    deadline = time.monotonic() + timeout
    row = None
    while time.monotonic() < deadline:
        try:
            with store.lane('plan').connection(read_only=True) as connection:
                row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (admitted.job_id,)).fetchone())
        except LaneError as error:
            if error.code != 'PROJECT_RECOVERY_REQUIRED':
                raise
            # Observe the same admitted job after its active commit finishes.
            # Persistent preparation still fails the original deadline; no
            # recovery, admission retry or mutation is performed here.
            time.sleep(.02)
            continue
        if row['state'] in {'verified', 'blocked'}:
            break
        time.sleep(.02)
    assert row is not None, 'Code worker did not publish a bounded terminal observation'
    assert row['state'] == 'verified', {key: row[key] for key in ('state', 'error_code', 'result_object')}
    system[0].delta.owned_completion(admitted.job_id).result(timeout=timeout)
    return json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']


def test_index_queries_and_impact_use_separate_lane_and_actual_worker(code_system):
    create_plan(code_system)
    result = execute(code_system)
    engine, store, _ = code_system
    assert result['files'] == 3 and result['changes']['added'] == 3
    assert not result['graph_generated'] and not result['source_bytes_mutated']
    assert engine.workers.status()['succeeded_operations'] >= 3
    args = {'snapshot_id': result['snapshot_id']}
    before = {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    search = call(code_system, 'code_query', args | {'query': 'greeting'})
    assert search.status == 'ok', search.error
    assert {row['path'] for row in search.result['result']['rows']} == {'app.py', 'helper.py'}
    symbols = call(code_system, 'code_query', args | {'collection': 'symbols', 'query': 'greeting'})
    assert symbols.result['result']['rows'][0]['fact']['qualified_name'] == 'greeting'
    dependencies = call(code_system, 'code_query', args | {'collection': 'dependencies'})
    assert dependencies.result['result']['rows'][0]['fact']['name'] == 'example-lib'
    impact = call(code_system, 'code_impact', args | {'paths': ['helper.py']})
    assert impact.status == 'ok', impact.error
    assert impact.result['result']['files'] == ['app.py', 'helper.py']
    assert impact.result['result']['runtime_dependency_completeness'] is False
    after = {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    assert before == after
    with store.lane('local_code').connection(read_only=True) as db:
        assert db.execute('SELECT count(*) FROM code_file_version').fetchone()[0] == 3
    with store.connection(read_only=True) as db:
        assert db.execute("SELECT count(*) FROM sqlite_schema WHERE name GLOB 'code_*'").fetchone()[0] == 0
    assert not (store.root / 'github_code/github_code_sector_v001.sqlite').exists()


def test_exact_replacement_retains_history_then_refreshes_changed_file(code_system):
    create_plan(code_system, ('code_index', 'code_apply', 'code_refresh'))
    first = execute(code_system)
    store = code_system[1]
    old_bytes = (store.source_root / 'helper.py').read_bytes()
    replacement = 'def greeting(name):\n    return "welcome " + name\n'
    changed = execute(code_system, 'code_apply', {'snapshot_id': first['snapshot_id'], 'filename': 'helper.py',
        'expected_sha256': hashlib.sha256(old_bytes).hexdigest(), 'replacement_utf8': replacement}, index=1)
    assert not changed['index_refresh_required']
    assert (store.source_root / 'helper.py').read_text(encoding='utf-8') == replacement
    old = call(code_system, 'code_read', {'snapshot_id': first['snapshot_id'], 'filename': 'helper.py'})
    assert 'hello' in old.result['result']['text']
    assert changed['index_refresh']['result']['changes'] == {'added': 0, 'modified': 1, 'deleted': 0, 'unchanged': 2}
    refreshed = execute(code_system, 'code_refresh', {'paths': ['.'], 'expected_snapshot': changed['snapshot_id']}, index=2)
    assert refreshed['snapshot_id'] == changed['snapshot_id']
    assert refreshed['changes'] == {'added': 0, 'modified': 0, 'deleted': 0, 'unchanged': 3}
    with store.lane('local_code').connection(read_only=True) as db:
        assert db.execute('SELECT count(*) FROM code_file_version').fetchone()[0] == 4
        assert db.execute('SELECT count(*) FROM code_snapshot').fetchone()[0] == 2
        assert db.execute('SELECT count(*) FROM code_mutation').fetchone()[0] == 1


def test_code_metadata_and_empty_graph_are_read_only_before_initialization(code_system):
    _, store, _ = code_system
    before = {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    result = call(code_system, 'code_current')
    assert result.status == 'ok' and result.result['result'] == {'scopes': [], 'initialized': False}
    graph = call(code_system, 'lane_view_preview', {'view_id': 'local_code.relationships'})
    assert graph.error.code == 'LANE_NOT_INITIALIZED'
    after = {p: p.read_bytes() for p in store.root.rglob('*.sqlite*') if p.is_file()}
    assert before == after


def test_schema_and_tool_routes_discover_code_without_installing_tools(code_system):
    response = call(code_system, 'toolchain_resolve', {'action': 'code_index'})
    assert response.status == 'ok' and response.result['resolution']['selected_route']
    assert not response.result['resolution']['execution_authorized']
    assert call(code_system, 'code_index', {'paths': ['.']}).error.code == 'DELTA_REQUIRED'


def test_parsers_preserve_python_scope_and_do_not_treat_toml_descriptions_as_dependencies():
    from evidence_lane_plugin.code_parsers import parse_code
    result = parse_code('sample.py', b'class A:\n    def f(self):\n        return print("x")\n')
    assert [row['qualified_name'] for row in result['facts']['symbol']] == ['A', 'A.f']
    assert result['facts']['call'][0]['callee'] == 'print'
    toml = parse_code('pyproject.toml', b'[project]\nname="hello"\ndescription="not-a-dependency"\ndependencies=["real-package>=2"]\n')
    assert [row['name'] for row in toml['facts']['dependency']] == ['real-package']
    invalid = parse_code('bad.py', b'def syntax error')
    assert invalid['parser_state'] == 'parse_diagnostics'
    assert invalid['facts']['parser_diagnostic']


def git_source(system):
    source = system[1].source_root
    for args in (['init', '-b', 'main'], ['config', 'user.name', 'Fixture'],
                 ['config', 'user.email', 'fixture@example.invalid'], ['config', 'core.autocrlf', 'false']):
        subprocess.run(['git', '-C', str(source), *args], check=True, capture_output=True)
    (source / '.gitattributes').write_bytes(b'*.py text eol=crlf\n')
    subprocess.run(['git', '-C', str(source), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(source), 'commit', '-m', 'Fixture checkpoint'], check=True, capture_output=True)
    registered = call(system, 'source_register', {'sources': [str(source)]})
    assert registered.status == 'ok', registered.error
    batch = registered.result['result']['source_authority']['batch_id']
    history = call(system, 'source_git_history', {'batch_id': batch, 'occurrence_ordinal': 1})
    assert history.status == 'ok', history.error
    return history.result['result']['snapshot_id']


def test_github_code_uses_exact_git_blobs_and_keeps_sources_history_separate(code_system):
    history = git_source(code_system)
    create_plan(code_system, ('code_index_git',))
    indexed = execute(code_system, 'code_index_git', {'paths': ['.'], 'git_snapshot_id': history})
    result = call(code_system, 'code_read', {'lane_id': 'github_code', 'snapshot_id': indexed['snapshot_id'], 'filename': 'helper.py'})
    assert result.status == 'ok', result.error
    source = code_system[1].source_root
    committed = subprocess.run(['git', '-C', str(source), 'show', 'HEAD:helper.py'], check=True, capture_output=True).stdout
    assert result.result['result']['sha256'] == hashlib.sha256(committed).hexdigest()
    assert indexed['git_reference']['snapshot_id'] == history
    with code_system[1].lane('github_code').connection(read_only=True) as db:
        assert db.execute('SELECT source_git_snapshot_id FROM code_git_reference').fetchone()[0] == history
        assert db.execute("SELECT 1 FROM sqlite_schema WHERE name='source_git_commit'").fetchone() is None


def test_conditional_code_graph_declares_real_semantics_and_does_not_publish_on_read(code_system):
    create_plan(code_system)
    indexed = execute(code_system)
    store = code_system[1]
    before = {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    graph = call(code_system, 'lane_view_preview', {'view_id': 'local_code.relationships',
        'scope': {'query': indexed['scope_id']}})
    assert graph.status == 'ok', graph.error
    kinds = {node['kind'] for node in graph.result['graph']['nodes']}
    assert {'code_repo', 'code_file', 'code_symbol', 'dependency_item', 'project_artifact'} <= kinds
    assert any(edge['kind'] == 'IMPORTS' for edge in graph.result['graph']['edges'])
    assert before == {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}

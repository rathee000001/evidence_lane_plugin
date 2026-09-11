from tests.test_canon_v4 import pair
from tests.test_delta_entry import plan, system
from tests.test_task_continuity import accept, offer

__all__ = ['pair', 'system']


def test_context_rejects_inconsistent_current_plan_dependencies_without_writes(pair):
    invoke, _sender, receiver, _source, _target, system = pair
    plan(system)
    offered, _ = offer(pair)
    accepted, _ = accept(pair, offered)
    assert accepted.status == 'ok', accepted.error
    engine, store, _, _ = system
    with engine.project_work.mutation(store) as lease, lease.transaction('plan') as connection:
        connection.execute("INSERT INTO plan_dependencies VALUES(1,'first','first')")
    before = {str(p): p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    response = invoke(receiver, 'continuation_context', {
        'continuation_id': offered.result['continuation_id'],
        'continuation_digest': offered.result['continuation_digest']})
    assert response.error is not None and response.error.code == 'CONTINUATION_PLAN_INTEGRITY', response
    assert response.result is None
    assert {str(p): p.read_bytes() for p in store.root.rglob('*') if p.is_file()} == before

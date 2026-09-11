"""Stdio MCP adapters execute current media routes; no installed-host claim."""
import asyncio
import base64
import io
import json

from evidence_lane_plugin.local_transport import LocalEndpoint
from evidence_lane_plugin.plan_runtime import PlanStore
from PIL import Image

from .test_media_profile_v4 import plan
from .test_native_workflow_bindings import native
from .test_pdf_media_export_refresh_v4 import media_system as media_system  # noqa: PLC0414
from .test_pdf_parsers_v4 import pdf_assets as pdf_assets  # noqa: PLC0414


def test_stdio_transform_export_preserves_originals_and_destination_history(media_system):
    engine, store, _ = media_system
    raw = (store.source_root / 'fixture.png').read_bytes()
    plan(media_system, ['media_index', 'media_transform', 'media_export'])

    def join():
        with engine._admission:
            assert engine._admission.wait_for(lambda: engine._background_jobs == 0, timeout=110)

    async def run():
        async with native(engine.root, store.project_id, permissions=('read', 'write', 'tools')) as session:
            async def read(action, arguments=None):
                response = (await session.call_tool(action, {'project_id': store.project_id,
                    'arguments': arguments or {}})).structuredContent
                assert response['status'] == 'ok', response
                return response['result']['result']

            previous = None
            for index, action in enumerate(['media_index', 'media_transform', 'media_export']):
                arguments = ({'filename': 'fixture.png'} if index == 0 else
                    {'snapshot_id': previous['snapshot_id'], 'expected_sha256': previous['sha256'],
                     'logical_name': 'small.tiff', 'output_format': 'TIFF', 'metadata': 'strip', 'width': 6, 'height': 4}
                    if index == 1 else {'snapshot_id': previous['snapshot_id'], 'filename': 'delivered.tiff'})
                task = PlanStore(store).task('media-' + str(index), expected_revision=1)
                response = (await session.call_tool('delta_enter', {'project_id': store.project_id, 'expected_revision': 1,
                    'arguments': {'task_id': task.definition.task_id, 'plan_revision': 1, 'contract_digest': task.contract_digest,
                        'action': action, 'arguments': arguments}})).structuredContent
                assert response['status'] == 'queued', response
                await asyncio.to_thread(join)
                with store.lane('plan').connection(read_only=True) as connection:
                    row = dict(connection.execute('SELECT * FROM delta_runs WHERE job_id=?', (response['job_id'],)).fetchone())
                assert row['state'] == 'verified', row
                produced = json.loads(store.lane('plan').read_object(row['result_object']))['result']['result']
                if index == 2:
                    assert not produced['source_index_refresh_required']
                    produced = produced['index_refresh']['result']
                previous = produced
                metadata = await read('media_query', {'snapshot_id': produced['snapshot_id']})
                assert metadata['raw_object'] == produced['sha256']
                if index == 1:
                    original = await read('media_read', {'snapshot_id': produced['snapshot_id'], 'representation': 'original_source'})
                    assert base64.b64decode(original['content_base64']) == raw
                    transformed = await read('media_read', {'snapshot_id': produced['snapshot_id']})
                    with Image.open(io.BytesIO(base64.b64decode(transformed['content_base64']))) as image:
                        assert image.format == 'TIFF' and image.size == (6, 4)
                if index == 2:
                    current = await read('media_current')
                    assert len(current['media']) == 2
            head = store.pv_head()
            forged = await session.call_tool('media_query', {'project_id': store.project_id,
                'arguments': {'snapshot_id': previous['snapshot_id'], 'codec_command': 'not allowed'}})
            assert forged.isError and store.pv_head() == head

    with LocalEndpoint(engine, studio_enabled=False):
        asyncio.run(run())
    assert (store.source_root / 'fixture.png').read_bytes() == raw
    with Image.open(store.source_root / 'delivered.tiff') as image:
        assert image.format == 'TIFF' and image.size == (6, 4)

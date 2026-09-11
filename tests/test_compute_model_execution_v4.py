"""Real CPU BGE/Delta/query execution with the current compute contracts."""
import contextlib
import json
import os

import pytest
from evidence_lane_plugin.flash_authority import SessionFlashAuthority

from . import test_code_profile_v4 as code_base
from .test_code_model_runtime_v4 import (
    test_real_bge_embeddings_and_all_cosine_readers_preserve_project_bytes as verify_bge,
)

pytestmark = pytest.mark.skipif(not os.environ.get('EVI_CODE_QUALIFICATION_ASSETS'),
    reason='Requires the explicitly staged pinned Code model; absence is not qualification.')


def test_current_compute_route_preserves_real_cpu_embeddings_and_read_only_queries(tmp_path, monkeypatch):
    monkeypatch.setenv('EVIDENCE_LANE_STUDIO_ROOT', os.environ['EVI_CODE_QUALIFICATION_ASSETS'])
    monkeypatch.setenv('HF_HUB_OFFLINE', '1')
    monkeypatch.setenv('HF_HUB_DISABLE_TELEMETRY', '1')
    with contextlib.contextmanager(code_base.code_system.__wrapped__)(tmp_path) as system:
        engine, store, _ = system
        SessionFlashAuthority().verify(registry=engine.registry)
        verify_bge(system)
        with store.lane('local_code').connection(read_only=True) as db:
            rows = db.execute('SELECT manifest_object FROM code_embedding_run').fetchall()
        assert len(rows) == 2
        for row in rows:
            value = json.loads(store.lane('local_code').read_object(row['manifest_object']))
            assert value['device'] == 'cpu'
            assert value['compute']['selection']['selected_provider'] == 'CPU'
            assert value['compute']['selection']['settings_revision'] == 0
            assert value['compute']['selection']['execution_state'] == 'not_executed'
            assert all(item['execution_state'] == 'executed' and item['selected_provider'] == 'CPU'
                       for item in value['compute']['worker_evidence'])

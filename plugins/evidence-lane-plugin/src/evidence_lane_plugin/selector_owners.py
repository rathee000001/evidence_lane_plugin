"""Exact original-byte and lineage bindings for the retained sector owners."""
from functools import partial

from . import (
    code_profile,
    document_profile,
    media_profile,
    pdf_profile,
    powerbi_profile,
    presentation_profile,
    sector_evidence_profile,
    tableau_profile,
    tabular_profile,
)
from .code_profile_schema import code_migrations
from .sector_support import sector_migrations
from .source_selectors import SelectorOwner, register_file_selector


def _manifest(reader, store, snapshot_id):
    return reader(store, snapshot_id)[0]


def _code_manifest(lane_id, store, snapshot_id):
    return code_profile._snapshot(code_profile.code_lane(store, lane_id), snapshot_id)[1]


def register_selector_owners(registry):
    for lane_id in ('local_code', 'github_code'):
        registry.register_selector(SelectorOwner(lane_id, 'code', 'scope_id', code_migrations(lane_id),
            partial(_code_manifest, lane_id), lambda value: {row['path']: row['sha256'] for row in value['files']},
            lambda value: value['paths']))
    for lane_id, prefix, key, module in (
            ('docs', 'doc', 'document_id', document_profile),
            ('ppt', 'ppt', 'presentation_id', presentation_profile),
            ('tableau', 'tableau', 'tableau_id', tableau_profile),
            ('power_bi', 'powerbi', 'powerbi_id', powerbi_profile),
            ('pdf_ocr', 'pdf', 'pdf_id', pdf_profile),
            ('images_ocr', 'media', 'media_id', media_profile)):
        register_file_selector(registry, lane_id, prefix, key, sector_migrations(lane_id),
            partial(_manifest, module.read_snapshot), member_field='source_members' if lane_id == 'power_bi' else None)
    for lane_id, prefix in (('data_excel', 'sheet'), ('data', 'data'), ('research', 'research'),
                           ('artifacts', 'artifact'), ('custom', 'custom')):
        module = tabular_profile if lane_id in ('data_excel', 'data') else sector_evidence_profile
        register_file_selector(registry, lane_id, prefix, 'source_id', sector_migrations(lane_id),
            partial(_manifest, lambda store, snapshot_id, module=module, lane_id=lane_id:
                module.read_snapshot(store, lane_id, snapshot_id)),
            source_field='original_source_object' if module is tabular_profile else 'raw_object')

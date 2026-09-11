"""Bindings for implemented sector owners, distinct from the definition catalog."""
from __future__ import annotations

from typing import get_args

from .code_profile_schema import code_migrations
from .document_schema import DOC_MIGRATIONS
from .errors import LaneError
from .lanes import get_lane, lane_artifact_contract, lane_family

IMPLEMENTED_SECTORS = ('local_code', 'github_code', 'docs', 'data_excel', 'data', 'ppt', 'tableau', 'power_bi', 'pdf_ocr', 'images_ocr', 'research', 'artifacts', 'custom')


def sector_package_folder(lane_id):
    """Source ownership is separate from a project's sectors/<lane> state."""
    if lane_family(lane_id) not in IMPLEMENTED_SECTORS or get_lane(lane_id).kind != 'sector':
        raise LaneError('SECTOR_RUNTIME_NOT_IMPLEMENTED', 'Select a retained implemented sector.')
    return 'authorities/project_sectors/' + lane_family(lane_id)


def _check_lane_action(lane_id, action_lanes, action, arguments):
    selected = arguments.get('lane_id', action_lanes.get(action))
    if action not in action_lanes or (selected != lane_id and not (lane_id == 'custom' and lane_family(selected) == 'custom')):
        raise LaneError('SECTOR_ACTION_MISMATCH', 'Use this sector entrypoint only for its own exact operation and lane.')


def read_sector_source(client, lane_id, action_lanes, *, project_id, action, arguments=None):
    """Original reader binding, through the same authenticated public SDK."""
    arguments = dict(arguments or {})
    _check_lane_action(lane_id, action_lanes, action, arguments)
    return client.call(action, project_id=project_id, arguments=arguments)


def build_sector_source(client, lane_id, action_lanes, *, project_id, task_id,
                        plan_revision, contract_digest, request_id=None):
    """Original builder binding for one exact sector task, never another engine.

    The read selects its stored operation; normal planned Delta admission owns
    grants, source bindings, worker execution and verification. No source path,
    operation argument or PASS claim can override that Plan contract here.
    """
    from .adaptive_delta_entry import PlannedDeltaEnter
    request = PlannedDeltaEnter(task_id=task_id, plan_revision=plan_revision, contract_digest=contract_digest)
    offset = 0
    while offset <= 2000:
        response = client.call('plan_read', project_id=project_id,
            arguments={'revision': plan_revision, 'offset': offset, 'limit': 100})
        if response.status != 'ok':
            return response
        page = response.result
        if page.get('revision') != plan_revision or page.get('current_revision') != plan_revision:
            raise LaneError('STALE_PLAN_REVISION', 'Select the current complete Plan before sector work.')
        for row in page.get('tasks', []):
            task = row['definition']
            if task['task_id'] != task_id:
                continue
            if row['contract_digest'] != contract_digest:
                raise LaneError('PLAN_CONTRACT_MISMATCH', 'The sector task contract changed.')
            operation = task.get('operation')
            if operation is None:
                raise LaneError('PLAN_OPERATION_REQUIRED', 'Adopt a bound sector operation before using this builder.')
            _check_lane_action(lane_id, action_lanes, operation['action'], operation['arguments'])
            return client.call('delta_enter_planned', project_id=project_id, expected_revision=plan_revision,
                arguments=request.model_dump(mode='json'), request_id=request_id)
        if not page.get('truncated') or not page.get('tasks'):
            break
        offset += len(page['tasks'])
    raise LaneError('PLAN_TASK_NOT_FOUND', 'The exact sector task is absent from the bounded Plan.')


def sector_migrations(lane_id):
    from .selector_schema import selector_migrations
    return (*_parser_sector_migrations(lane_id), *selector_migrations(lane_id))


def _parser_sector_migrations(lane_id):
    if lane_family(lane_id) in {'research', 'artifacts', 'custom'}:
        from .sector_evidence_schema import migrations
        return migrations(lane_id)
    if lane_id == 'images_ocr':
        from .media_schema import MEDIA_MIGRATIONS
        return MEDIA_MIGRATIONS
    if lane_id == 'pdf_ocr':
        from .pdf_schema import PDF_MIGRATIONS
        return PDF_MIGRATIONS
    if lane_id == 'power_bi':
        from .powerbi_schema import POWERBI_MIGRATIONS
        return POWERBI_MIGRATIONS
    if lane_id == 'tableau':
        from .tableau_schema import TABLEAU_MIGRATIONS
        return TABLEAU_MIGRATIONS
    if lane_id in ('local_code', 'github_code'):
        return code_migrations(lane_id)
    if lane_id == 'docs':
        return DOC_MIGRATIONS
    if lane_id == 'ppt':
        from .presentation_schema import PPT_MIGRATIONS
        return PPT_MIGRATIONS
    if lane_id in {'data_excel', 'data'}:
        from .tabular_schema import tabular_migrations
        return tabular_migrations(lane_id)
    raise LaneError('SECTOR_RUNTIME_NOT_IMPLEMENTED', 'This sector has a definition but no registered runtime binding yet.')


def sector_actions(registry, lane_id, *, schemas=None):
    selected = []
    for schema in registry.schemas() if schemas is None else schemas:
        spec = registry.get(schema['name'])
        field = spec.input_model.model_fields.get('lane_id')
        if (spec.name in {'source_snapshot_state', 'source_snapshot_retire'} or
                field is not None and (lane_id in get_args(field.annotation) or lane_family(lane_id) == spec.profile == 'custom') and spec.profile in {'code', 'document', 'spreadsheet', 'data', 'presentation', 'tableau', 'power_bi', 'pdf_ocr', 'images_ocr', 'research', 'artifacts', 'custom'}):
            selected.append(schema)
    return selected


def sector_package_contract(lane_id, registry):
    lane = get_lane(lane_id)
    migrations = sector_migrations(lane_id)
    body = {'schema': 'evidence-lane.sector-package.v4', 'lane_id': lane_id, 'folder': lane.folder,
        'source_package_folder': sector_package_folder(lane_id), 'folder_role': 'external_project_state',
        'database': lane.database_relative_path, 'files': lane.files_relative_path,
        'schema_history': lane.schema_history_relative_path, 'runtime_module': 'evidence_lane_plugin.code_profile',
        'parser_modules': ['evidence_lane_plugin.code_parsers', 'evidence_lane_plugin.code_toolchain'],
        'retrieval_module': 'evidence_lane_plugin.code_semantic',
        'migrations': [{'owner': item.owner, 'version': item.version, 'digest': item.digest} for item in migrations],
        'actions': sector_actions(registry, lane_id), 'source_selector': registry.selector_owner(lane_id).schema(),
        'artifact_contract': lane_artifact_contract(lane_id),
        'toolchain': 'toolchains/operation-toolchains.v4.json', 'source_byte_store': 'owning_lane_files',
        'historical_git_owner': 'sources', 'full_runtime_installation_verified': False,
        'project_data_packaged': False}
    if lane_family(lane_id) in {'research', 'artifacts', 'custom'}:
        from .sector_evidence_profile import format_contract
        body.update(runtime_module='evidence_lane_plugin.sector_evidence_profile',
            parser_modules=['evidence_lane_plugin.sector_evidence_parsers', 'evidence_lane_plugin.sector_evidence_workers',
                'evidence_lane_plugin.selected_sqlite_parser'],
            retrieval_module='evidence_lane_plugin.sector_evidence_profile', historical_git_owner=None,
            formats=format_contract(lane_id), render_module=None,
            view_module='evidence_lane_plugin.sector_evidence_views',
            views=[registry.get_view(lane_id + '.structure').schema()])
        if lane_id == 'research':
            body['parser_modules'].extend(['evidence_lane_plugin.research_web_content',
                'evidence_lane_plugin.research_web_fetch', 'evidence_lane_plugin.research_web_workers',
                'evidence_lane_plugin.research_discovery', 'evidence_lane_plugin.research_discovery_transport',
                'evidence_lane_plugin.research_discovery_workers'])
    if lane_id == 'docs':
        from .document_profile import format_contract
        body.update(runtime_module='evidence_lane_plugin.document_profile',
            parser_modules=['evidence_lane_plugin.document_parsers', 'evidence_lane_plugin.document_workers'],
            retrieval_module='evidence_lane_plugin.document_profile', historical_git_owner=None,
            formats=format_contract(), render_module='evidence_lane_plugin.document_rendering')
    if lane_id == 'power_bi':
        from .powerbi_profile import format_contract
        body.update(runtime_module='evidence_lane_plugin.powerbi_profile',
            parser_modules=['evidence_lane_plugin.powerbi_parsers', 'evidence_lane_plugin.powerbi_workers',
                'evidence_lane_plugin.powerbi_native', 'evidence_lane_plugin.powerbi_process',
                'evidence_lane_plugin.powerbi_pbix_child', 'evidence_lane_plugin.powerbi_json_schema',
                'evidence_lane_plugin.powerbi_authoring'],
            retrieval_module='evidence_lane_plugin.powerbi_profile', historical_git_owner=None,
            formats=format_contract(), render_module=None)
    if lane_id == 'pdf_ocr':
        from .pdf_profile import format_contract
        body.update(runtime_module='evidence_lane_plugin.pdf_profile',
            parser_modules=['evidence_lane_plugin.' + name for name in ['pdf_parsers', 'pdf_forms',
                'pdf_workers', 'pdf_native', 'pdf_process', 'pdf_child', 'pdf_authoring', 'pdf_ocr', 'pdf_enrichment']],
            retrieval_module='evidence_lane_plugin.pdf_profile', historical_git_owner=None,
            formats=format_contract(), render_module='evidence_lane_plugin.pdf_rendering')
    if lane_id == 'images_ocr':
        from .media_profile import format_contract
        body.update(runtime_module='evidence_lane_plugin.media_profile',
            parser_modules=['evidence_lane_plugin.' + name for name in ['media_parsers', 'media_transform',
                'media_workers', 'media_native', 'media_process', 'media_child', 'media_ocr', 'media_derivatives']],
            retrieval_module='evidence_lane_plugin.media_profile', historical_git_owner=None,
            formats=format_contract(), render_module=None)
    if lane_id in {'data_excel', 'data'}:
        from .tabular_formats import format_contract
        body.update(runtime_module='evidence_lane_plugin.tabular_profile',
            parser_modules=({'data_excel': ['evidence_lane_plugin.spreadsheet_parsers', 'evidence_lane_plugin.spreadsheet_workers'],
                'data': ['evidence_lane_plugin.structured_data']}[lane_id]),
            retrieval_module='evidence_lane_plugin.tabular_profile', historical_git_owner=None,
            formats=format_contract(lane_id), render_module='evidence_lane_plugin.spreadsheet_rendering' if lane_id == 'data_excel' else None)
    if lane_id == 'ppt':
        from .presentation_profile import format_contract
        body.update(runtime_module='evidence_lane_plugin.presentation_profile',
            parser_modules=['evidence_lane_plugin.presentation_parsers', 'evidence_lane_plugin.presentation_workers',
                            'evidence_lane_plugin.presentation_authoring'],
            retrieval_module='evidence_lane_plugin.presentation_profile', historical_git_owner=None,
            formats=format_contract(), render_module='evidence_lane_plugin.presentation_rendering')
    if lane_id == 'tableau':
        from .tableau_profile import format_contract
        body.update(runtime_module='evidence_lane_plugin.tableau_profile',
            parser_modules=['evidence_lane_plugin.tableau_parsers', 'evidence_lane_plugin.tableau_workers',
                'evidence_lane_plugin.tableau_hyper', 'evidence_lane_plugin.tableau_hyper_child',
                'evidence_lane_plugin.tableau_authoring'],
            retrieval_module='evidence_lane_plugin.tableau_profile', historical_git_owner=None,
            formats=format_contract(), render_module=None)
    return body

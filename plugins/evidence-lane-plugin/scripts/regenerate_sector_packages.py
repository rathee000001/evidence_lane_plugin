"""Project each implemented sector's actual schema, runtime and action bindings."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / 'src'))
sys.path.insert(0, str(PLUGIN / 'scripts'))

from sector_package_assets import compile_lane_assets

from evidence_lane_plugin.sector_support import (
    IMPLEMENTED_SECTORS,
    sector_migrations,
    sector_package_contract,
    sector_package_folder,
)


def generate(registry, *, check=False):
    outputs = {}
    def encode(value):
        return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    for lane_id in IMPLEMENTED_SECTORS:
        folder = sector_package_folder(lane_id)
        migrations = sector_migrations(lane_id)
        sql = '-- Projection only: the engine applies these owner migrations under its project writer.\n'
        sql += '\n'.join(f'-- {item.owner} v{item.version}, digest {item.digest}\n' +
            '\n'.join(statement.rstrip(';') + ';' for statement in item.statements) for item in migrations) + '\n'
        outputs[folder + '/schema.sql'] = sql.encode('utf-8')
        outputs[folder + '/migration-history.json'] = encode({'lane_id': lane_id,
            'migrations': [{'owner': item.owner, 'version': item.version, 'description': item.description,
                           'digest': item.digest, 'statements': item.statements} for item in migrations]})
        outputs[folder + '/runtime.py'] = f'''"""{lane_id}: the canonical Code operation and schema bindings."""
from evidence_lane_plugin.code_profile import impact_code, query_code, read_code, read_current
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = {lane_id!r}

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return read_current(project, LANE_ID)

# Mutations enter through the shared registry and exact Delta contract.
__all__ = ['LANE_ID', 'impact_code', 'inspect', 'migrations', 'query_code', 'read_code']
'''.encode()
        if lane_id == 'docs':
            outputs[folder + '/runtime.py'] = b'''"""Docs: separate native document, query and schema bindings."""
from evidence_lane_plugin.document_profile import current_documents, query_document, read_document
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'docs'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_documents(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_document', 'read_document']
'''
        elif lane_id == 'images_ocr':
            outputs[folder + '/runtime.py'] = b'''"""Images/media: owning native structure, derivatives and schema bindings."""
from evidence_lane_plugin.media_profile import current_media, query_media, read_media
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'images_ocr'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_media(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_media', 'read_media']
'''
        elif lane_id in {'research', 'artifacts', 'custom'}:
            outputs[folder + '/runtime.py'] = f'''"""{lane_id}: owning evidence facts, immutable files and schema bindings."""
from evidence_lane_plugin.sector_evidence_contracts import Selection, model_for
from evidence_lane_plugin.sector_evidence_profile import current, query, read
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = {lane_id!r}

def migrations(lane_id=LANE_ID):
    selected = model_for(LANE_ID, Selection)(lane_id=lane_id)
    return sector_migrations(selected.lane_id)

def inspect(project, lane_id=LANE_ID):
    return current(project, model_for(LANE_ID, Selection)(lane_id=lane_id))

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query', 'read']
'''.encode()
        elif lane_id == 'ppt':
            outputs[folder + '/runtime.py'] = b'''"""PPT: separate native presentation, retrieval and schema bindings."""
from evidence_lane_plugin.presentation_profile import (
    current_presentations,
    query_presentation,
    read_presentation,
)
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'ppt'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_presentations(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_presentation', 'read_presentation']
'''
        elif lane_id == 'tableau':
            outputs[folder + '/runtime.py'] = b'''"""Tableau: separate native document/extract, retrieval and schema bindings."""
from evidence_lane_plugin.sector_support import sector_migrations
from evidence_lane_plugin.tableau_profile import current_tableaus, query_tableau, read_tableau

LANE_ID = 'tableau'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_tableaus(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_tableau', 'read_tableau']
'''
        elif lane_id == 'power_bi':
            outputs[folder + '/runtime.py'] = b'''"""Power BI: separate model/report/data, retrieval and schema bindings."""
from evidence_lane_plugin.powerbi_profile import (
    current_powerbis,
    query_powerbi,
    read_powerbi,
    read_powerbi_schema,
)
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'power_bi'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_powerbis(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_powerbi', 'read_powerbi', 'read_powerbi_schema']
'''
        elif lane_id == 'pdf_ocr':
            outputs[folder + '/runtime.py'] = b'''"""PDF/OCR: owning native structures, page OCR, forms and derivatives."""
from evidence_lane_plugin.pdf_profile import current_pdfs, query_pdf, read_pdf
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'pdf_ocr'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_pdfs(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_pdf', 'read_pdf']
'''
        elif lane_id in {'data_excel', 'data'}:
            outputs[folder + '/runtime.py'] = f'''"""{lane_id}: the owning tabular operations and schema bindings."""
from evidence_lane_plugin.sector_support import sector_migrations
from evidence_lane_plugin.tabular_contracts import Selection, model_for
from evidence_lane_plugin.tabular_profile import current, query, read

LANE_ID = {lane_id!r}

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current(project, model_for(LANE_ID, Selection)())

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query', 'read']
'''.encode()
        outputs.update(compile_lane_assets(registry, lane_id))
        contract = sector_package_contract(lane_id, registry)
        outputs[folder + '/lane-contract.v4.json'] = encode({
            'schema': 'evidence-lane.installed-lane-contract.v4', 'lane_id': lane_id,
            'source_package_folder': folder, 'project_state_folder': contract['folder'],
            'database': contract['database'], 'files': contract['files'],
            'schema_history': contract['schema_history'], 'migrations': contract['migrations'],
            'artifact_contract': contract['artifact_contract'],
            'actions': [row['name'] for row in contract['actions']],
            'shared_toolchain': contract['toolchain'], 'project_data_packaged': False})
        outputs[folder + '/manifest.schema.json'] = encode({
            '$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object',
            'required': ['schema', 'lane_id', 'source_package_folder', 'folder', 'folder_role',
                         'database', 'files', 'migrations', 'actions', 'members', 'project_data_packaged'],
            'properties': {'schema': {'const': 'evidence-lane.sector-package.v4'},
                'lane_id': {'const': lane_id}, 'source_package_folder': {'const': folder},
                'folder': {'const': contract['folder']}, 'folder_role': {'const': 'external_project_state'},
                'members': {'type': 'array', 'minItems': 1}, 'project_data_packaged': {'const': False}}})
        contract['members'] = [{'path': name, 'sha256': hashlib.sha256(body).hexdigest()}
                               for name, body in outputs.items() if name.startswith(folder + '/')]
        outputs[folder + '/manifest.v4.json'] = encode(contract)
    outputs['authorities/project_sectors/sector-runtime-registry.v4.json'] = encode({'schema': 'evidence-lane.sector-runtime-registry.v4',
        'implemented_sectors': [{'lane_id': lane, 'manifest': sector_package_folder(lane) + '/manifest.v4.json'}
                                for lane in IMPLEMENTED_SECTORS],
        'other_sector_definitions': 'schemas/lane-registry.v4.json', 'installation_inferred': False})
    changed = []
    for name, body in outputs.items():
        path = PLUGIN / name
        if path.exists() and path.read_bytes() == body:
            continue
        changed.append(name)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
    if check and changed:
        raise RuntimeError('Sector packages require regeneration: ' + ', '.join(changed))
    return {'implemented_sector_packages': len(IMPLEMENTED_SECTORS), 'changed': changed}

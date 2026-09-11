"""Docs: separate native document, query and schema bindings."""
from evidence_lane_plugin.document_profile import current_documents, query_document, read_document
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'docs'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_documents(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_document', 'read_document']

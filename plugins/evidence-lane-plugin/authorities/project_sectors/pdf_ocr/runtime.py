"""PDF/OCR: owning native structures, page OCR, forms and derivatives."""
from evidence_lane_plugin.pdf_profile import current_pdfs, query_pdf, read_pdf
from evidence_lane_plugin.sector_support import sector_migrations

LANE_ID = 'pdf_ocr'

def migrations():
    return sector_migrations(LANE_ID)

def inspect(project):
    return current_pdfs(project)

__all__ = ['LANE_ID', 'inspect', 'migrations', 'query_pdf', 'read_pdf']

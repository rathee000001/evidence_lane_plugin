---
description: Read the PDF and OCR source lane
argument-hint: <project_id> [query-or-path]
---

# /evi-12-pdf-ocr

Resolve canonical lane `pdf_ocr` through `lane_catalog`, call `lane_status`,
then use bounded `lane_search` or `lane_fetch`. Report pages, text blocks,
images, OCR evidence, hashes, and review regions. Missing OCR capability is a
visible blocker, never an implied pass.

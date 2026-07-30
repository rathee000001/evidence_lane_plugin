---
description: Read the Images and OCR source lane
argument-hint: <project_id> [query-or-path]
---

# /evi-13-images-ocr

Resolve canonical lane `images_ocr` through `lane_catalog`, call
`lane_status`, then use bounded `lane_search` or `lane_fetch`. Report metadata,
OCR engine state, OCR evidence, review regions, and exact source hashes.
Metadata-only parsing is not OCR success.

# PDF and OCR

PDF/OCR owns its own SQLite, original PDFs and immutable derivatives. Use
`pdf_index`/`pdf_refresh` for exact files and page budgets; the default text
backend is selected by the operation's tool routes. `pdf_query` keeps native
page/geometry/form facts and separate OCR lines distinct. `pdf_read` returns
bounded exact bytes. For scans, `pdf_ocr` defaults to text-poor selected pages;
it preserves native text on mixed documents. Read confidence and review regions
through `pdf_ocr_read`; confidence does not prove recognition accuracy.

`pdf_generate` accepts closed page layouts with text, tables, local image bytes
and interactive fields. `pdf_edit` binds an exact snapshot and hash, verifies
both the canonical field tree and page widgets, and preserves originals.
Text edits must fit the field's maximum length and the font in its generated
appearance; unsupported glyphs are rejected instead of accepted as question marks.
Flattening, unique orphan-widget repair and signed-source derivatives are
explicit choices; XFA editing and cryptographic signature validation are not
implemented. `pdf_render`/`pdf_render_read` produce bounded page PNGs for visual
inspection. `pdf_export` parses proposed bytes with the destination's current
native backend and page/table limits, journals the exact granted file write,
then renews Sources, destination facts and any existing lane view. Use
`index_refresh.result.snapshot_id` for the destination; the top-level snapshot
identifies the exported input. Existing OCR, rendering and enrichment remain
bound to their original snapshots. Export does not rerun them or establish layout
equivalence. If refresh fails after writing, preserve the confirmed effect and
prior selectors and reconcile without automatic replay. Both the destination
intake limit and the explicit Plan budget apply.
`pdf_enrich` adds a completed offline Docling layout/table projection; an
incomplete conversion is rejected without publishing. `pdf_enrichment_read`
reads that exact separate projection. `pdf_ocr.structure` preserves page,
field/widget and OCR locators in distinct MMD, DOT and pointer files.

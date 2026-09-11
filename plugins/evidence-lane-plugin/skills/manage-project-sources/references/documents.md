# Word documents

Documents own a separate Docs database and versioned natural files. Use
`document_index` for DOCX/DOTX, ODT and supported UTF-8 text formats; use
`document_convert` explicitly for bounded legacy DOC/RTF conversion. Conversion
preserves the exact original and publishes a derived DOCX with stated limits.
Read `document_current`, then query one exact snapshot through `document_query`
or fetch bounded bytes with `document_read`. Native structure, Docling enrichment
and page rendering are distinct results. `document_enrich` records only a
completed conversion; `document_enrichment_read` returns attributed excerpts.

`document_generate` creates a lane-owned DOCX. `document_edit` replaces selected
plain paragraphs under exact snapshot, content hash and expected text bindings;
tracked changes are explicit and complex fields/links are not silently flattened.
Both produce new document versions. `document_export` writes a granted project
filename only with its expected hash or expected absence, retaining before bytes.
It parses the proposed bytes before writing, then renews Sources, the exact
destination snapshot and its existing lane view in one coordinated publication.
Continue from `index_refresh.result.snapshot_id`; the top-level snapshot remains
the exported version. A new filename owns a separate source identity. Parent
source routes and assertions remain bounded by the Plan read scope; the binary
payload consumes its explicit input/output budget. A post-write refresh failure
keeps the confirmed effect and blocks acceptance without automatic replay.
Use `document_refresh` for changed modern source bytes, or another exact
`document_convert` for changed legacy input. `document_render` requires the
verified shared office runtime and creates PDF/page PNG artifacts. Read them
with `document_render_read` and inspect every page before making layout claims.
Do not equate conversion completion with Word layout equivalence. The conditional
`docs.structure` MMD/DOT/pointer view links native items; it does not supply page
numbers or replace natural document artifacts.

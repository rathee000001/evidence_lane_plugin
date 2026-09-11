# PowerPoint presentations

Presentations own a separate PPT database. `presentation_index` and
`presentation_refresh` preserve exact PPTX/PPTM/POTX/PPSX bytes, declared slide
order, editable objects, notes, tables, image references and relationships.
Use `presentation_convert` explicitly for bounded PPT/ODP conversion; the
original stays separate from the derived PPTX and conversion is not lossless.
`presentation_current`, `presentation_query` and `presentation_read` retrieve
one exact version with bounded results. `presentation_enrich` creates a
separate successful Docling projection; `presentation_enrichment_read` returns
attributed excerpts without rerunning it.

`presentation_generate` authors native editable text, shapes, rectangular
tables, embedded PNG/JPEG images and speaker notes, with dimensions in inches.
`presentation_edit` binds text replacements to the snapshot, content hash,
part, paragraph index and expected text. It refuses complex fields, links and
mixed run formatting. A supplied slide order must name every slide exactly
once. Edits retain untouched package members and publish a new version.
`presentation_export` separately writes one granted project filename under its
expected hash or absence, after parsing the proposed bytes. It renews Sources,
the exact destination snapshot and already selected `ppt.structure` formats in
one coordinated publication. Use `index_refresh.result.snapshot_id` for the
destination; original, converted and edited input snapshots remain addressable.
The bounded parent source observations and binary payload require the matching
Plan path and byte budgets. Reconcile a blocked post-write refresh from its
confirmed effect journal; do not automatically replay it.
`presentation_render` uses the verified shared
Impress runtime to produce PDF/page PNGs; inspect every page before layout
claims. `presentation_render_read` verifies the stored artifacts. PowerPoint
layout equivalence, animation/media playback and chart generation are not
claimed. Rendering refuses macro packages, embedded OLE/controls, embedded
chart workbooks, external resources other than hyperlinks, and executable
actions. Fonts may be substituted; hidden slides follow the converter's
default. Intake still preserves these packages without executing them.
The conditional `ppt.structure` MMD/DOT/pointer view links exact slide,
notes and object locators; it is separate from rendered slide pages.

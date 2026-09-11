# Tableau

Tableau owns a separate database and immutable TWB/TDS/TWBX/TDSX/Hyper bytes.
`tableau_index` and `tableau_refresh` preserve source XML and package members,
record calculations, relationships and live connection metadata, and inspect
bounded typed Hyper samples across every schema. They never open the recorded
external files or live connections. TDE intake retains opaque bytes only.
`tableau_query` uses exact typed locators or bounded literal FTS5/BM25 search;
Hyper results include total row counts and explicit sample limits. `tableau_read`
can return the original file or an exact package member by byte range.

`tableau_generate` creates native Hyper extracts from bounded typed tables.
`tableau_edit` changes only selected captions or formula attributes, bound to
the snapshot, source hash, native XML item and expected value; it publishes a
new version and retains untouched package members. XML edits do not establish
valid Tableau calculations or visual equivalence. `tableau_export` writes the
whole snapshot under the destination's expected hash or absence, then renews
Sources, the destination snapshot and any selected view together. It parses
the proposed bytes before writing and preserves destination Hyper/sample options.
Use `index_refresh.result.snapshot_id` for the renewed destination. The
`tableau.structure` MMD/DOT/pointer view carries document and extract locators,
not rendered charts. Native Hyper operations use the pinned Windows backend.

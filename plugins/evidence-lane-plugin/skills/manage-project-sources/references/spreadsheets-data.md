# Excel and structured data

Excel and structured data own separate databases and versioned files.
Use `spreadsheet_index` for native XLSX/XLSM/XLTX/XLTM structures and
`spreadsheet_index_values` for the bounded XLS/XLSB/ODS value reader. The latter
does not extract formula expressions or formatting. Use `data_index` for
CSV/TSV/JSON/JSONL, `data_index_arrow` for Parquet, Arrow IPC files/streams and
Feather V2. Each route declares its actual native or shared dependency.
Read the corresponding `*_current`, `*_query` and `*_read` operations with the
exact owning lane and snapshot. A sampled row prefix is not a complete dataset.
Source refreshes require the matching `*_refresh` route and previous snapshot.

`spreadsheet_generate` authors XLSX; `spreadsheet_edit` replaces exact existing
cells in a new version, preserving untouched package members and clearing stale
formula caches. `spreadsheet_recalculate` explicitly publishes a Calc-written
version with possible format changes. `spreadsheet_render` and its read action
produce separate PDF/page PNGs. Inspect the pages before making layout claims;
neither Calc completion nor cached values prove Microsoft Excel equivalence.
Native formula locators cover syntactic A1 ranges, not all dynamic dependencies.
Numeric generation and replacement reject precision beyond Excel's 15 digits;
use explicit text for longer identifiers. Protected sheets/workbooks, merged or
validated cells, rich text, formula-controlled ranges and extended cell metadata are refused
by the cell replacement action. Generated dates use ISO values; timezone and
submillisecond time values are refused.

`data_generate` creates bounded JSON/JSONL/CSV/TSV. `data_transform` selects,
filters, sorts or explicitly casts a complete dataset while retaining typed
input lineage. JSON decimals, nulls and missing keys stay distinct; CSV values
remain strings, and CSV output represents missing/null as empty fields.
Numeric filters compare decimal values while preserving their source spelling.
Empty JSON has no recoverable column names; requested or selected columns remain
in the operation evidence. Transforms use the bound immutable facts, including
Arrow-source facts, without silently adding an undeclared reader dependency.
The named `data_inspect_pandas`, `data_inspect_polars`, `data_inspect_duckdb`,
`spreadsheet_inspect_openpyxl` and `spreadsheet_inspect_pandas` actions record
attributed library observations on a bounded immutable sample. Their inspection
read actions do not rerun the libraries or refresh sources.

Both lanes use a journaled `*_export` bound to the granted destination's exact
hash or absence. Export parses the proposed bytes before writing, then refreshes
Sources, the destination snapshot/natural file and any existing lane view in one
coordinated publication. Continue with `index_refresh.result.snapshot_id`; the
top-level `snapshot_id` still identifies the exported historical version. A new
filename has its own source identity. Known parent source occurrences and
assertions remain ordered and separately addressable. Allow sufficient Plan
input/output budget for the bounded binary payload and verification. A failure
after the confirmed write preserves the effect journal and blocks completion;
do not automatically replay it. The conditional `data_excel.structure` and
`data.structure` views retain their selected scope and MMD/DOT/pointer formats.

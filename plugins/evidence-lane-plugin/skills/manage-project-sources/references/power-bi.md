# Power BI

Power BI owns its separate database, immutable source files and derived versions.
Use `powerbi_index` or `powerbi_refresh` with an exact primary filename and any
explicit `companion_files`; project references never trigger automatic reads.
PBIP/PBIR/PBISM, BIM/TMDL, PBIX/PBIT and admitted project ZIPs have distinct
fidelity. The native TOM adapter reads model definitions; the isolated PBIX
reader returns actual stored-model metadata and bounded table samples controlled
by `max_rows_per_table`. Neither runs DAX/M, refreshes a model, opens a live
connection or proves report rendering. Legacy report layouts remain read-only.

`powerbi_schema` lists the exact packaged Microsoft schema URIs or reads one
complete schema; supplied URLs are not fetched. `powerbi_generate` accepts a BIM
database document or a ZIP containing explicit UTF-8 project/report definitions
and optional `resources_base64`. `powerbi_edit` binds the current snapshot and
each changed member to its prior hash. A null member hash means add-if-absent;
omitting both content representations means delete an existing hash-bound
member. Model and PBIR changes are validated, unselected members stay identical,
and a new version is published. These operations do not rewrite PBIX/PBIT files.

`powerbi_query` reads typed facts (source properties are under `data`) or bounded
literal FTS5/BM25 matches. `powerbi_read` pages through exact snapshot bytes or
package members. `original_source` returns the primary admitted source, while
`original_member` returns an exact original companion even after a derivative
edit. `powerbi_export` requires the destination's current hash or confirmed
absence, parses proposed bytes with the destination's model/entrypoint/sample
options, then renews Sources, destination facts and any selected view together.
An exported ZIP is one newly observed file with archive-member provenance;
original companions remain bound to their admitted snapshot. Companions retain
their 8 MiB file limit; a whole ZIP may explicitly select `max_file_bytes` up to
16 MiB. Source refresh applies bounded archive expansion before the file effect.
Use `index_refresh.result.snapshot_id` for the renewed destination. Failed
post-write refresh keeps the confirmed effect and previous selectors for recovery.
The conditional
`power_bi.structure` view carries exact model/report locators and source versus
native-metadata locator meanings; its MMD/DOT/pointer files are not report charts.

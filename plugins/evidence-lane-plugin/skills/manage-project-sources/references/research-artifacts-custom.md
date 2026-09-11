# Research, Artifacts and Custom data

For multiple Custom lanes, register each `custom__<name>` instance with
`custom_lane_configure`, choosing its explicit retained parser, accepted file
extensions, byte bound and selected SQLite table/row bounds. Sources keeps the
immutable adapter contract; each instance owns its database, files and schema
history. Inspect current or historical contracts with `custom_lanes_read`.
Use profile `custom` and the existing Custom actions with the exact `lane_id`,
`parser` and returned `adapter_contract` in the Plan task arguments. Source
preparation accepts those parser options under the named lane's `lane_options`.
Version a registration with its exact `expected_contract`, then refresh affected
Plan contracts before continuing; stale adapter selections stop before parsing.
The source-schema compiler still maps sealed metadata through `source_schema_map`
and `source_schema_configure`. Named lanes do not load arbitrary parser code or
replace the separate optional-connector grant workflows. Their navigation view
is `<lane_id>.structure`, with each export kept in that instance's folder.

Research, Artifacts and Custom keep separate source snapshots and typed facts.
For their local files use `research_index`, `artifacts_index` or `custom_index`;
use the corresponding `_index_media` action for images, SVG or audio/video.
These actions run within the selected Plan task. Supply its exact current
`expected_snapshot` when refreshing, retain the selected Sources occurrence
receipt, and use the matching `_refresh` or `_refresh_media` operation. The engine
chooses the format parser and its declared tools. An explicit PDF backend keeps
its own fidelity; do not silently retry another parser after invocation.
Use each lane's `_current`, `_query` and `_read`, or the shared lane search/fetch
readers, for bounded historical evidence. Reads do not recheck live sources.
Research question/finding/citation labels and Custom decision/next labels are
unvalidated source assertions. Artifacts preserves file metadata, notebook cells,
saved-output hashes and actual media probes. PowerPoint packages use the native
presentation parser and retain its slide, note, shape and relationship locators.
Native text extracts retain their exact hash and fact reference without duplicate
search entries. A missing text extract has an explicit Artifact review record;
archive filenames and media metadata alone do not establish content extraction.
Notebook code and imported SQL are
passive; saved outputs are not verified computations. ZIP intake inspects only
its directory and reports unsafe members without extracting them. Selected
SQLite intake rejects an active WAL/journal and requires a complete bounded
DELETE-journal image. It inspects fixed metadata and only explicitly named
ordinary-table rows; it does not query imported views or virtual tables.

For a requested source relationship export, discover `research.structure`,
`artifacts.structure` or `custom.structure` through `lane_view_catalog`.
Preview the exact source binding, using `scope.query` for a source ID, and export
only the requested MMD/DOT or lane-specific navigation pointer. Research keeps
local files, captured pages, saved-byte extraction provenance and unvisited
search results distinct. Artifacts lists extracted evidence, passive archive
members and review states. Custom preserves source assertions and inspected
SQLite tables, foreign keys and selected row locators. These views neither
validate claims nor apply source decisions to the Plan. Read existing exports
to detect stale source bindings; `lane_view_refresh` is the explicit refresh.

For a selected web URL, use `research_web_capture` within the current Research
task and its live network grant. HTTPX and Requests are ordered readiness choices
before invocation; a failed request does not trigger a retry or another transport.
Bind a repeated capture to the URL's exact `expected_snapshot`. Response wire
bytes, decoded body, redirects and observation times stay in the Research lane.
The engine does not execute page scripts or follow links and child resources.
Use `research_web_extract` for a richer projection of a saved HTML snapshot.
Its default `auto` selects the first ready declared algorithm within the task's
permitted tools; an explicit extractor keeps that selection exact. Preserve the
current URL selector. Extraction is offline and retains the original capture.
Use the returned stored-source `logical_name` for shared `lane_fetch`; the URL
is separate provenance. Research's existing readers search both local files and
web snapshots. Historical reads do not claim the remote page is still current.
Captured HTML citations use the first declared HTTP(S) base URL for relative
links. Inspect `citation_resolution` in the snapshot metadata for the chosen
basis and omitted links. Unsupported bases do not produce guessed relative
citations. These are static resource URLs without fragments; browser DOM repair,
CSP and script-driven base changes are outside this saved-byte projection.
Use `research_web_discover` for a bounded source search through the selected DDGS
providers. It preserves the provider response bundle and attributed snippets in
Research; a result URL is an unvisited locator, not an ingested target document.
Provider rejections and partial coverage stay explicit. Search results do not
validate their claims. Inspect the typed Research citations, select relevant
URLs, and capture those targets as separate source-intake operations. Each
provider has measured availability only when that actual request succeeds.
Observe each action's byte, item, table and row limits. Unknown binary formats
retain exact bytes with a review state; that is not successful content extraction.
These local parsers do not establish working web fetch/discovery, rich HTML
extraction or a complete project refresh. Check those distinct action routes.

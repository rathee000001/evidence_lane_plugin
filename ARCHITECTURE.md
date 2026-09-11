# Evidence Lane v4 architecture

Evidence Lane v4 has two local deliverables. The Codex-managed plugin contains
the skills, SDK, native MCP adapter and executable engine. The Windows Studio is
a separately built, visible read-only observer served by that same engine.
Studio cannot create projects, change Plans, submit work, configure tools,
mutate connectors, select compute or perform recovery.

The current typed engine registry exposes 294 operations through one generated
schema, SDK and MCP set. Twenty-four first-class skills route user intent into
those operations. The tool catalog contains 104 retained declarations. Catalog
membership never proves installation, readiness or execution; each selected
operation records its actual route, tool result and verification evidence.

## Project state

Every selected project keeps its state outside the installed package. Root PV
coordinates project identity, the writer fence, lane heads, publication and
recovery references. It does not store the business records of another lane.

Eight authority lanes own separate SQLite databases, files and schema history:

- Plan
- ChatLineage
- Canon
- Project Memory
- Learning
- Sources
- Receipts
- Universe

Thirteen sector lanes keep the same physical separation:

- local and GitHub code
- Word documents
- PowerPoint presentations
- Excel workbooks
- structured data
- Tableau
- Power BI
- PDF and OCR
- images, audio and video
- research and web evidence
- generated artifacts
- custom sources and selected SQLite databases

Office scope is Word, PowerPoint and Excel. Access, OneNote, Visio, Outlook,
Microsoft Project and Publisher have no profile, registration, schema, worker,
fixture or exclusive dependency in the current package. OneDrive is storage,
not an Office application or sector.

## Runtime and installation

The engine owns one writer per project and bounded worker processes shared by
multiple clients. Windows first detection validates the exact sparse plugin
source and SHA-256-bound component assets from one immutable release tag,
materializes offline environments under `C:/Apps/EvidenceLaneStudio` (or the
explicit `EVIDENCE_LANE_STUDIO_ROOT`), verifies every immutable installed file,
registers the short native Studio launcher and re-enters MCP through the
installed release.

The source release binding remains disabled until the later candidate asset
build and immutable tag. This repository is therefore development source, not
an installed-runtime or public-release claim.

## Release sequence

The current order is: finish source and Studio integration, complete the final
purge, run security and complete-candidate qualification, commit the exact
candidate, install through the public repository route, verify native behavior,
then build and publish the public site and distribution material. Merge to
`main` and final Goal completion remain explicit user decisions.

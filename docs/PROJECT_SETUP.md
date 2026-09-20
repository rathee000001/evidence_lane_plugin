<p align="center">
  <a href="README.md">Docs</a> · <a href="QUICKSTART.md">Quickstart</a> · <a href="INSTALL.md">Install</a> · <a href="PROJECT_SETUP.md">Project setup</a> · <a href="WORKFLOW_GUIDE.md">Workflows</a> · <a href="STUDIO.md">Studio</a> · <a href="INTEGRATIONS.md">Integrations</a> · <a href="SDK_AND_MCP.md">SDK & MCP</a> · <a href="TROUBLESHOOTING.md">Troubleshooting</a> · <a href="RELEASES.md">Releases</a> · <a href="CONTRIBUTORS.md">Contributors</a>
</p>

# Project setup

A project has two different homes: the source folder you work with and the durable Evidence Lane state that records how the project is connected. Keeping them separate protects source ownership and makes recovery clearer.

## Choose the project explicitly

Do not infer a project from a task title, terminal folder, or recently opened directory. Select the exact source root and the exact durable state root.

The state root stores project-owned records and addressed files. The shared engine and Studio stay under the shared installation root and serve more than one project.

## Register the relationship

Use **Choose project storage** to register:

- the project display name;
- source root;
- state root;
- read-only or writable intent;
- sensitivity label when applicable;
- current client/session relationship.

Registration does not copy the source tree into the shared runtime.

## Add selected sources

Use **Bring in project sources** to choose the first source group. Evidence Lane records the selection and prepares work appropriate to the source type.

All core project records are initialized for a registered project. Source-specific records appear only when that source family is actually selected and used. This keeps an unused project from being filled with unrelated databases.

## Source families

- Local and GitHub code
- Word documents
- PowerPoint presentations
- Excel workbooks and structured data
- Tableau and Power BI material
- PDFs and OCR
- Images and media
- Research and web sources
- Generated artifacts
- Explicit custom data and selected SQLite

Support differs by operation and format. Registration, parsing, indexing, export, and native application fidelity are separate claims.

## Build the first Plan

Use **Shape the project workflow** to propose an approach from the selected sources and requested outcome. Use **Create or update the Plan** to adopt or steer the actual task sequence.

A useful Plan states:

- what the current task must produce;
- which sources and tools it may use;
- what checks prove the task result;
- what would stop the work;
- what happens next.

## Open Studio

Studio should show the same selected project and current Plan reported by the engine. If it shows no project, a stale revision, or a different source root, treat that as a connection or registration problem rather than a cosmetic issue.

## Project images

The coordinated Studio redesign plans a project-card cover owned by the project-registration and artifact pipeline. The read-only Studio will display the engine-supplied image and status. Until that later release is implemented and verified, do not infer that every registered project already has a generated cover.

## Safe setup boundary

Setup is complete only when the project registration, selected source identities, Plan reference, engine connection, and Studio readback agree. A source file existing on disk is not proof that it was registered or prepared.

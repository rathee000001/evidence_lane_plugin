# Tableau sector package

This original source owner is `authorities/project_sectors/tableau`. Live project state belongs to `tableau` under the selected external project root. The packaged SQLite is an empty, unbound schema template; the engine creates identities and applies exact migrations under its writer. It is never opened as project state.

`builder.py` admits one stored, hash-bound sector Plan operation through the authenticated SDK. `reader.py` admits only this lane's typed read actions. Both use the same public SDK and engine; `runtime.py` exposes the actual owning migration and read functions. A queued builder response is not completion. Read the normal Delta result and acceptance receipt.

`schema.sql`, `migration-history.json` and `schema-template.json` describe the actual owner migrations. `tableau.mmd` and `tableau.dot` are matching schema traversal maps. `workflow.v4.json`, `workflow.mmd` and `workflow.dot` describe current action routes and their gates. They contain no project facts or runtime readiness assertion.

Natural document/media/data files and selected project MMD/DOT/navigation pointers remain in their own lane. The view contracts in `tools.json` define each consumer, exact locator meaning, optional formats and refresh rules. Use the first-class Source Intake references for parser fidelity, exports and verified refresh procedures.

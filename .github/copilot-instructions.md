# Evidence Lane v4 repository instructions

Repository contributor guidance in this file applies to public source. Keep
local governance records, runtime state, project databases and development
receipts outside the public tree. Work only on the requested branch and
preserve existing dirty bytes and Git history. Do not reset, stash, clean,
force-push, publish, deploy, install or merge unless the active task explicitly
owns that action.

The executable product lives in `plugins/evidence-lane-plugin`; the Windows
read-only Studio lives in `apps/evidence-lane-studio`; the public site is a
separate later delivery under `apps/evidence-lane-app`. Generated SDK, MCP,
schema, skill and package members must reconcile with the current typed engine
registry.

Use `.github/maintainer-ci.v4.json` and `scripts/maintainer_ci.py` for selected
repository checks. Install Python dependencies from the hash-locked root files.
Treat tests and catalog entries as evidence within their stated scope, never as
installation, native-runtime, publication or user-approval proof.

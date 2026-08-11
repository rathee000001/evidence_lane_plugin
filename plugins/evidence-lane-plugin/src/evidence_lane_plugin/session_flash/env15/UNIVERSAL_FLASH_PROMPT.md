# Evidence Lane universal session flash

This authority is loaded when the Evidence Lane plugin is selected. It governs
session behavior only. It is not project source, a project version, an Entry
Slip, an Exit Slip, a task, a HIL decision, or permission to write remotely.

## Identity and persistence

- The active product identity is Evidence Lane. "Plugin" describes the host delivery type; it is not part of the product display name.
- The plugin remains installed until the user removes it.
- The ENV15 and UOP15 authorities are installation-scoped and outside every PV.
- A repeated boot with the same authority digest reuses the verified flash.
- A changed authority digest fails closed; it is never silently substituted.

## Locked authority

- Treat bundled ENV15 and UOP15 SQLite files as read-only and immutable.
- Verify the exact SHA-256 member set, locked Mermaid hashes, SQLite integrity,
  foreign keys, and SQLite user version before governed work.
- ENV may be changed only through a separately authorized `flash_env` boundary.
- UOP may be changed only through a separately authorized `flash_uop` boundary.
- UOP cannot override ENV, the selected project, an accepted PV, or a human HIL.
- Add Delta; do not drop Delta.

## Governed execution

- Use one authenticated user, one authorized workspace, one selected project,
  one accepted PV pointer, one agent, one bounded task, and one execution line.
- Recover accepted state from exact project evidence before reasoning or change.
- Record only visible, operational, reproducible ChatLineage evidence.
- Never record secrets, credentials, uncontrolled temporary data, or private
  model reasoning.
- Never infer HIL approval from a prompt, successful test, valid hash, candidate,
  or prior approval.
- Only an explicit `APPROVE` decision advances the accepted PV pointer.
- Candidate approval does not authorize a remote Git write.
- Remote Git action requires a bounded branch grant and a prepared receipt.
  A still-valid standing grant may auto-execute a matching push to its exact
  non-protected branch; protected branches and merges remain separately gated.

## Host boundary

- Codex Desktop or durable local CLI may operate on the authorized local source.
- Codex VM and ephemeral Codex execution must persist sealed PV artifacts and
  bounded receipts to the configured user-owned durable store.
- Headless Codex API invocations reverify Flash at each entry and do not require
  an interactive-host tunnel.
- ENV/UOP bytes never enter `code.sqlite`, a PV directory, Git patch, or remote
  repository write.

## Boot result

The boot response must expose the authority version, authority digest, flash
action, persistence boundary, and exact warning state. A partial source packet
may supply an independently verified ENV/UOP subset, but the incomplete packet
must never be described as intact.

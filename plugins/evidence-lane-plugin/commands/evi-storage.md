---
description: Inspect or explicitly select the project primary storage connector without changing the six-control router.
---

Load the `evi-storage` skill. Inspect with `storage_connector_inspect`. Change a
selection only with `storage_connector_select` and its exact
`SELECT_STORAGE:<MODE>[:connector-id]` token. Local SQLite remains primary when
durable local storage exists unless the project explicitly selects a configured
transactional connector. Google Drive is an optional sealed-artifact mirror,
never the primary runtime authority.

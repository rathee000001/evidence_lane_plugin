# receipts source owner

Current executable source package: `authorities/receipt_ledger`. Storage: receipts/receipts_authority_v001.sqlite with files and schema history in receipts. The package binds the canonical engine implementations listed in `runtime-binding.v4.json`.

`builder.py` and `reader.py` use the same authenticated SDK and engine registry as skills and native MCP. Builders admit only their named ordinary mutations; planned operations retain normal stored-Plan Delta admission. Engine grants, task bindings, writer ownership, stale-contract checks and acceptance rules still apply. Queued responses are not verified completion. Instructions and Studio reads do not gain mutation privileges.

Schema and workflow graphs are source projections. They are not runtime receipts or project facts. Project MMD, DOT, navigation pointers and natural artifacts remain distinct, consumer-selected lane files. The exact view contracts describe their locators, formats, freshness and refresh behavior. Tool requirements are shared references and do not prove installation.

`receipt-ledger.sqlite` is a newly generated empty, unbound template. It contains no project identities, receipts or business records and is never opened as live state. `receipt-ledger.mmd` and `receipt-ledger.dot` describe its schema. The engine applies the actual owner migrations under a writer and records schema history in the selected external lane.

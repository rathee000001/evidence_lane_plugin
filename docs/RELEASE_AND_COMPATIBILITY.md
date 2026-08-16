# Release and compatibility

The current source line is Evidence Lane 2.2.0 on
`agent/evi-v220-systemwide-release-hil-v2.2.0`. It advances from accepted PV12
without rewriting PV12, historical packages, prior accepted PVs, receipts,
State Travel packages, or earlier version labels.

Source identity, local package identity, installed-host identity, unaccepted
candidate identity, and accepted Project Truth are separate facts. A README,
version string, Git commit, test, preview, package, or installed cache cannot
substitute for its missing receipt.

The intermediate PV13 gate may Fuse PV13 only after a fresh exact approval. It
does not automatically merge `main` or authorize later presentation work. A
later, separately authorized release gate governs main promotion, final public
site refresh, and release-channel normalization.

Downstream governed projects keep their own Git, CI, deployment, storage,
schema, and plugin choices. Their PVs do not trigger the Evidence Lane plugin
maintainer release cycle.

Compatibility evidence explains ancestry; it never overwrites current source,
installed package, Plan, pointer, or HIL truth.

# Claude/Fable reference reconciliation

## Evidence inspected

- reference repository head:
  `76E77945AEE28E55E8718F767EDA5F423303E784`;
- R&D V2 commit:
  `1437013EB06A4CA73713FC201D0553B2F3F445E4`;
- major prior candidate commit:
  `12B05CFFA6F9789504CB66AEB6F9A6639BA0C1BD`;
- attached full lineage SHA-256:
  `051FAF161EEAEA50975893985F416D0E82E5F8A3B72101C4FC28BAC31274074E`.

The lineage was read completely. Narrative claims were not treated as proof;
the corresponding repository changes were inspected separately.

## Deltas retained

1. Explicit return from `REJECTED_RUN` or `FAILED_RUN` to the prior accepted
   state, with pointer-unmoved evidence.
2. Exact continuation from correction/research pending states instead of
   leaving them as dead ends.
3. A visible distinction between accepted-PV entry truth and current post-edit
   source truth.
4. Concise progress wording with explicit changed-file and validation evidence.
5. Progressive retrieval across chunks, symbols, and paths.
6. Bounded line/symbol fetch, import visibility, and exact source provenance.
7. Explicit authority labels so candidate review cannot masquerade as accepted
   project truth.
8. Runtime version strings derive from one constant; declarative package and
   plugin versions are checked against it.

## Deltas not copied

- warning-only external ENV flash;
- unpinned external flash prompt;
- flash behavior that permits boot when authority is missing;
- non-atomic persistence writes;
- path handling without the existing Evidence Lane store boundary;
- a seal verifier weaker than the current exact-member package validator;
- claims that narration or fixture success proves a live HIL.

The Evidence Lane implementation keeps its stronger hash-locked session flash,
atomic writes, exact package membership, fail-closed transitions, and existing
source/PV engine.

## Research boundary

The attached lineage and R&D V2 commit raise valid future benchmark questions:
retrieval-oracle precision/recall, paired counterfactual replay, ephemeral
base-plus-overlay indexing, cost per passed task, evidence-audit score,
schema/tool overhead, amortized build cost, prompt-cache baselines, and VM
snapshot comparison. They are recorded as research questions only. In
particular, the overlay is not silently represented as implemented in this HIL.
These designs do not yet prove token, quality, or commercial advantage.

Commit 6 is the final requested reference checkpoint. No further reference
scan is part of this HIL.

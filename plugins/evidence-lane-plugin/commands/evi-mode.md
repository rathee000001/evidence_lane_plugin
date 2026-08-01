---
description: Classify ordered operating-mode intersections and explicit custom modes
argument-hint: [modes and optional custom brief]
---

# /evi-mode

Call `mode_classify` without changing the lifecycle position. Preserve the
user's mode order, always include Mode and Chat Lineage, and map known modes to
the locked ENV15 namespace. An unknown mode is never guessed: require a short
explicit name, concrete brief, and ordered canonical lanes, then persist only
that visible custom schema. Never store hidden reasoning.

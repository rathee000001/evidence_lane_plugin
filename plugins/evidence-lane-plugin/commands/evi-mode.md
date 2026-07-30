---
description: Classify one or more intersecting ENV15 modes and canonical lanes
argument-hint: <project_id> <request> [D,AL,PL,CD,OP,VAL,RS,JD,XL,PPT,DOC,PB,ENG,CE,RCV,X]
---

# /evi-mode

This single command may be fired at any Evidence Lane lifecycle position.
Call `mode_classify` with the user's exact request and any explicit ordered
mode list.

The locked namespace is Discussion, Analysis, Planning, Code, Output,
Validation, Research, Job/JD, Excel, Presentation, Document, Project Brain
Builder, Project Engulf, Clean Exit, Recovery, and Custom. Preserve
intersections such as Analysis + Planning or Forensic Audit + Planning + Code
HIL. Code reports the locked recursive guard `D -> PL -> CD -> VAL`.

Return each selected mode beside its canonical `/evi` lane command. The
internal `mode` authority remains schema-compatible, but `/evi-mode` is its
only user-facing command and is never presented as source intake. Always
include the `mode` and `chat_lineage` lanes. When a governed session is active,
append only a privacy-minimized `mode.classified` receipt to Chat Lineage.
Never store raw private reasoning.

When Planning (`PL`) is selected, also append a privacy-minimized,
hash-chained Planning-mode event to the task-backlog control plane and rebuild
its derived SQLite Plan runtime projection. This automatic append records the
selected mode IDs, canonical lanes, lifecycle position, pointer generation,
and request SHA-256. It does not queue a Delta, change a task status, or write
the canonical Plan source sector. `/evi-08-plan` remains the explicit exact-file
source-intake route; `/evi-50-task-plan` remains the only task-queue route.

This command does not classify a bounded task, create or accept a candidate,
move a pointer, infer HIL, or change the active lifecycle position. After the
classification, return to the exact prior command and wait.

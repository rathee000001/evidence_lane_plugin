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

This command does not classify a bounded task, create or accept a candidate,
move a pointer, infer HIL, or change the active lifecycle position. After the
classification, return to the exact prior command and wait.

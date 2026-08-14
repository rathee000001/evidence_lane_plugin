---
name: evi-learning
description: "Govern the project-isolated AI Agent Learning arm: inspect and retrieve accepted lessons, seal evidence-backed learning candidates, run the separate Learning HIL, and revoke accepted learning without changing Project Truth. Use when an AI workflow proposes a reusable procedural, failure-avoidance, relational, tool-routing, or host-compatibility lesson."
---

# Evidence Lane Agent Learning

First apply the shared installed lifecycle contract in
`../evidence-lane-code-lifecycle/SKILL.md`; this skill narrows that contract to
the separate Agent Learning authority and never widens lifecycle permission.

Agent Learning is a separate project-scoped AI learning authority under the
locked ENV/UOP boundary. It is not Project Truth, Canon Input, ChatLineage, a
Formula Engine operator, autonomous training, or the Project PV pointer. Brain
scaling means bounded indexed retrieval and composition, never self-training.

## Inspect and retrieve

Run `pv_status`, `pv_task_backlog`, and one bounded `pv_query` before using a
Learning result in governed work. Use `learning_inspect` to read the independent
candidate/event/pointer authority. Use `learning_retrieve` with exact scope,
time, conflict, and result limits. Keep the returned Learning slice visibly
separate from accepted Project Truth; do not concatenate or silently rerank the
two authorities.

No hit is a valid result. Expired, rejected, failed, superseded, revoked,
out-of-scope, or Project-Truth-conflicting lessons must remain excluded with an
explicit reason.

## Seal a candidate

Use `learning_seal_candidate` only for an evidence-backed visible lesson with:

- tier and lesson type;
- bounded task/project scope selectors;
- evidence and counterevidence references;
- outcome and calibrated confidence;
- contradiction and supersession links;
- observed, valid-from, and optional expiry times;
- privacy class and exact ChatLineage head.

Sealing creates `PENDING_LEARNING_HIL`; it does not accept the lesson, create a
Project candidate, invoke Project HIL, or move the Project pointer.

## Learning HIL

Use `learning_decide_candidate` only for the separate exact Learning six-way
decision surface: `APPROVE`, `APPROVE_WITH_DELTA`, `MORE_RESEARCH`, pointer-only
Learning rollback, `REJECT`, or `FAIL`. The decision may move only the Learning
pointer. It cannot promote Project Truth or authorize Project Fuse, Git,
install, deployment, Canon acceptance, or State Travel.

Use `learning_revoke` to append a revocation for accepted Learning. Preserve the
candidate, acceptance, pointer, retrieval, and revocation history. Never delete
or rewrite an older lesson.

## SDK and operator boundary

The internal provider-neutral SDK exposes the whole engine and its isolated
authority contracts; it is not a public skill and must not merge authority
arms. The Formula Engine compiles and routes bounded ENV/UOP operators; it does
not learn. Hooks transport visible lifecycle events only; they do not decide or
promote Learning.

## Exit receipt

Return the Learning candidate or retrieval identities, scope, evidence and
counterevidence references, lifecycle state, Learning pointer effect, conflict
suppression, and explicit proof that Project Truth and Project HIL were
untouched.

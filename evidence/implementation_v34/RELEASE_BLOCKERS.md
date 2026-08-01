# Release and HIL blockers

The implementation and local test gates pass, but release/HIL must carry these
facts forward:

1. **Manual OpenAI key revocation required.** A key exposed in the source
   conversation is compromised. No callable OpenAI Platform revocation
   capability is available in this task. The key was not echoed, read, or
   stored by this implementation. Production deployment using it is forbidden.
2. **Literal backlog-byte requirement not met.** Appending Deltas 022–036
   reserialized the live task-backlog JSON and backfilled derived fields on the
   original 21 entries. Semantic identity, order, contracts, queued status, and
   event-chain presence are retained, but byte-for-byte preservation of the old
   representation cannot be claimed.
3. **Installed MCP transport degraded.** The installed Evidence Lane MCP
   tunnel returned HTTP 404 during this task. Governed local APIs and read-only
   store verification were used; a genuinely fresh Codex task must prove native
   pickup after exact-SHA installation.
4. **Exact-SHA install and Vercel preview are pending at commit time.** These
   can only be tested after the one governed commit exists. The final external
   Exit Slip/addendum must distinguish pass, fail, and manual HIL blockers.

None of these facts authorizes Fuse, pointer movement, main merge, or State
Travel. The final candidate must remain UNACCEPTED until a later exact
case-sensitive `APPROVE` is supplied for that same displayed candidate.

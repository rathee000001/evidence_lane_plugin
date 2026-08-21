# Evidence Lane 0.7.0 release blockers

The local implementation and test gates pass, but the following external gates
remain open until separately verified:

1. The v0.7 branch must be committed once, pushed through its own exact one-use
   governed Git confirmation, and proven equal locally and remotely.
2. Codex must install that exact Git SHA through the marketplace/update route;
   a local development virtual environment is not installation evidence.
3. The ChatGPT durable MCP origin, OAuth/JWT scopes, durable storage, and queue
   are not configured in the available environment.
4. A Vercel branch preview must be built from the final v0.7 Git SHA and report
   the same SHA. The older preview is not v0.7 release evidence.
5. ChatGPT connection UI confirmation remains manual HIL because screen control
   is not authorized in this task.
6. The OpenAI key disclosed in the earlier source conversation remains a hard
   manual release blocker until revocation is independently confirmed. The key
   is not reproduced, saved, or deployed.
7. The final v0.7 project-version candidate must remain unaccepted until the
   next six-way HIL. Tests, installation, and deployment do not accept it.

State Travel is not a release step here. Its prepared accepted-PV handoff stays
unconsumed unless the user explicitly requests travel or the host context is
genuinely exhausted.

Verdict: **FIX-THEN-PURSUE**. Confidence: **99%**. Evidence that would change
the verdict is a complete set of same-SHA push/install/deploy receipts, durable
origin health and auth proof, key-revocation confirmation, and human acceptance
of the fresh candidate.

# Delta 001-046 implementation traceability

The accepted ordered Delta 001-045 evidence remains byte-preserved through
[`DELTA_001_045_TRACEABILITY.md`](DELTA_001_045_TRACEABILITY.md) and its linked
001-043 predecessor. This additive document neither renumbers nor reopens those
accepted tasks.

| Order | Delta | Required implementation evidence | Verification boundary |
| ---: | --- | --- | --- |
| 046 | `EL-CHATGPT-PUBLIC-RUNTIME-INSTALL-CLEANUP-HIL-DELTA-046` | public Next.js Evidence Lane site; exact rewrites for `/mcp`, `/healthz`, and protected-resource discovery; exact-SHA durable-origin gate; semantic SQLite-derived MMD/DOT for all 18 lanes; v1.0 Git/package/docs update; exact Codex and ChatGPT install receipts; post-install POC and forensic audit | the site must render HTML, but it does not prove MCP readiness; ChatGPT stays blocked unless durable HTTPS/OAuth/storage/queue and same-SHA health pass; obsolete installs are removed only after replacement proof; PV3 remains unaccepted |

## Active implementation order

1. Build and validate source, the public site, and semantic topology.
2. Run the full suite plus the 18-lane dummy/incremental/render audit.
3. Create and push one governed v1.0 commit; verify local/remote SHA parity.
4. Install that exact SHA in Codex and deploy only the ChatGPT edge to Vercel.
5. Verify the exact ChatGPT installation before removing an obsolete app.
6. Run the independent post-install POC and forensic audits, including the
   separate Rathee Intelligence Lab Git-lane audit.
7. Seal a fresh **UNACCEPTED** PV3 and stop at the six-way HIL.

## Decision boundary

Delta completion, tests, Git push, Vercel deployment, plugin installation, POC,
and forensic audit are release evidence. None implies PV3 acceptance. No work in
this Delta may Fuse PV3, advance the accepted pointer beyond PV2, merge the new
branch into main, or invoke State Travel. Only a later exact case-sensitive
`APPROVE` bound to the displayed PV3 candidate may authorize Fuse.

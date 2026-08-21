# Public website to Git Markdown source map

The Evidence Lane website is a read-only projection of Git-tracked public
documentation. Every current route renders a visible source-document link.
The route story may simplify the document for business readers, but it cannot
introduce a lifecycle, version, capability, authority, or release claim that
the bound Markdown does not support.

| Website route | Git-tracked authority |
| --- | --- |
| `/` and `/readme` | `README.md` |
| `/skills` | `docs/SKILLS.md` |
| `/mcp` | `docs/MCP.md` |
| `/hooks` | `docs/HOOKS.md` |
| `/commands` | `docs/COMMANDS.md` |
| `/architecture` | `ARCHITECTURE.md` |
| `/lanes` | `docs/ARCHITECTURE.md` |
| `/operators` | `docs/HOST_STORAGE_ENV_MODE_CONTINUITY.md` |
| `/studio` | `README.md` |
| `/proof` | `docs/IMPLEMENTATION_TRACEABILITY.md` |
| `/provenance` | `docs/UPSTREAM_REFERENCE_PROVENANCE.md` |
| `/connect` | `docs/HOST_CAPABILITY_MATRIX.md` |
| `/hil` | `docs/FIRST_HIL_RUNBOOK.md` |
| `/privacy` and `/security` | `SECURITY.md` |
| `/terms` | `docs/TERMS_AND_CONDITIONS.md` |
| `/license` | `LICENSE.md` |
| `/copyright` | `docs/COPYRIGHT.md` |
| `/third-party` | `plugins/evidence-lane-plugin/THIRD_PARTY_NOTICES.md` |
| `/credits` | `docs/CREDITS_AND_CONTRIBUTIONS.md` |
| `/support` | `README.md` |
| `/helper` | `docs/USER_HELPER_GUIDE.md` |
| `/tunnel` | `docs/USER_TUNNEL_GUIDE.md` |

Clean CI validates that every public route has exactly one mapping, every
mapped path exists and is Git-tracked in the correction commit, the source
strip is present in the shared layout, and the 3.0 release identity is
consistent across the repository and site.

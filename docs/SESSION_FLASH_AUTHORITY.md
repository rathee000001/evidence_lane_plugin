# Session flash authority

## Accepted boundary

Plugin selection injects one universal behavior prompt. Before every governed
session, `SessionFlashAuthority` verifies a minimized 16-member ENV15/UOP15
authority bundle. The first valid boot creates an installation-scoped
verification receipt; later boots reuse the same digest. Boot separately
attaches the Flash context and visible capture runtime. `/evi-exit-boot`
detaches that runtime while the verification receipt and immutable authority
remain installed; a later Boot must re-verify and explicitly reattach.

The flash is not a PV member, project source file, Git Delta, ChatLineage payload,
or HIL decision. It cannot move a pointer or imply acceptance.

## Exact authority anchors

| Authority | SHA-256 |
| --- | --- |
| Flash manifest | `49B150FAC223C2198C3D60F273330D1EEE27FA961BFCD4CC47198ED31821266A` |
| Universal flash prompt | `E5751173A1419137DF57ED4811D82D0ADED227C6D064A0DF603EF7813F539D5F` |
| Combined authority digest | `64976104F181BF215B40315FCB33F9EC1730C51EF24B778B7F85698AF5F93972` |
| ENV MMD | `360C9878658106A24CFE60E0CDD98BB95EA8BAABB7229C84C9D9163F397981F1` |
| ENV SQLite | `78EEC5EFF7BA82DF38DF62ED65F2E8A4B8E1F3A593B8387779EAD7EA45E03810` |
| ENV law | `FC410F6EED4EDB10C8BC2969C4556324CEAAB0CEE98F3163D30657D724A72433` |
| UOP MMD | `7D9A51F7B29D26B2B7AB120A08D7CF504AB86C2FEFE709B9C2681E32ACCD1189` |
| UOP SQLite | `DB2539AAC36BE38D89C74D052C4764ECD28E4CFA29EFBF4EB6B0E4234CB1377F` |
| UOP law | `C4CB11039D3ECF1861FAE04654897CF25962905A8F5A8ACBC99439D3ED0B03CF` |

The manifest independently pins every MMD, DOT, SVG, PNG, SQLite, lock, law,
prompt, and source-audit member. Both SQLite files open only with
`mode=ro&immutable=1`; integrity, foreign keys, and `user_version=15` must pass.

After full verification, Evidence Lane builds a derived read-only runtime
projection keyed by the authority digest, plugin projection schema, and host
ABI. A content-addressed installed bundle may reuse the projection only after
the locked source verification and projection-manifest/hash checks pass. A
mutable development checkout rebuilds it. The projection lives under the
installation data root, never in source, ChatLineage, or PV bytes.

## Parent-packet forensic result

The supplied parent packet cannot be called intact:

- 134 files are declared;
- 133 members appear in its payload tree;
- 129 files actually exist;
- six declared `codex/` members are absent;
- one undeclared research file is present;
- every present declared ENV/UOP member matches its recorded size and hash.

Therefore only the independently verified ENV/UOP subset is accepted. Every
status and boot result retains `SOURCE_PACKET_PARTIAL_INTEGRITY`; it is a bounded
source warning, not a failure of the independently sealed subset.

Any member addition, deletion, size change, hash change, SQLite integrity
failure, lock mismatch, or authority-digest change fails closed before session
boot.

## Version 2 remote-Git policy precedence

Version 2 preserves every ENV15/UOP15 and universal-prompt byte above so an
installed active session does not silently migrate its Flash authority. The
universal prompt's v1.5 sentence requiring a one-use remote-push confirmation
is retained only as a hash-locked compatibility byte. The v2
`scripts/codex-release-channel.json` policy is the effective remote-Git rule:
the exact sole registered non-protected test branch requires a prepared receipt
but no per-push token; main push, merge, PR acceptance, force, and credential
intake remain forbidden. SessionStart includes that sealed effective policy and
its SHA-256 in `PLUGIN_RUNTIME_ENVELOPE`. A missing or mismatched v2 policy fails
the release identity rather than rewriting Flash bytes.

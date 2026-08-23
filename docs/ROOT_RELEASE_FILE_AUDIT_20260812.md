> Historical receipt only. Superseded for the current 3.0.0 source line by
> `CURRENT_ROUTE_FILE_REFRESH_RECEIPT_20260823.md` and its JSON authority.

# Root release-file audit — Evidence Lane 2.0.0

This receipt audits every tracked repository-root file visible in the GitHub
listing raised during Task147. It is based on source commit
`b315347d94fad031d3f4d6307e6bf0c6fa24b92f` plus the bounded root-policy
correction recorded in the commit that contains this receipt.

The audit is content-based. A file is changed only when its current 2.0.0
meaning is wrong; Git timestamps are not refreshed by no-op edits.

| Root file | SHA-256 at audit | 2.0.0 classification | Current correction |
| --- | --- | --- | --- |
| `.dockerignore` | `5B05F1D3EC228AE9B3101E85DA959BFF1B252BF9A8523CF646CA1F9A62906486` | Current build-context exclusion law | Byte-unchanged |
| `.env.example` | `761945DA1FAAA2225A82261260DB9C4D44CE1A60D6E45AEFC86865C98185CEE4` | Codex-local plus optional headless/API configuration | Corrected; retired ChatGPT/Vercel adapter fields removed |
| `.gitattributes` | `CCEF2E7065FB5850AB6E0B2188E33A4AE0CA579541B4D086979BCF625DB11EA4` | Current repository text/binary attributes | Byte-unchanged |
| `.gitignore` | `32ACB2B148A0D2465552723F0361705C1F50F2ECB516D5E88198E4FD9DEAF43B` | Current local/runtime exclusion law | Byte-unchanged |
| `.vercelignore` | `2880E554EB80E160F7E077B154C4D8A1F948EEDA92EF175631E0975574B3B806` | Public documentation-site deployment exclusions | Byte-unchanged; Vercel is not an MCP authority route |
| `COPYRIGHT.md` | `2FDC652BBFA779E3AC23DC48D327114C90885F33A0E4B8861067232C6F53A8E5` | Version-neutral ownership record | Byte-unchanged |
| `Dockerfile` | `AC8C41B00C67F128E203B3EFB88A0AF17AC6A7F532FDB2C56E1BE12F9846ACC3` | Exact-commit optional headless/API container | Byte-unchanged; not the retired ChatGPT adapter |
| `LICENSE.md` | `4C170D2A729885312D8F0CB757BEF1360F8EC857729FD7510AF000E83300BCCA` | Version-neutral proprietary license | Byte-unchanged |
| `README.md` | `21B1418B9B07A15A527895D3D7CF690AA87A7C74A1A426E5E554CD6D4D76B80F` | Current 2.0.0 product, branding, tunnel, Git, and install guide | Corrected in parent Task147 commit |
| `SECURITY.md` | `69DAE6D38E8B5F17D0CD4EB05385F6AEB388CF9822B1E041E211602A3A8B545B` | Current Codex/headless/tunnel/Git security boundary | Corrected; active thin-ChatGPT-adapter claim removed |
| `pyproject.toml` | `EC47E4528CB05DA34F505E50DA501DF7B8E0F945701C3DA66CB962A1778AD5AD` | Active package identity `2.0.0` | Byte-unchanged |
| `requirements.in` | `D66F5E8E355485E2FC449F3A584EFB4F04727B0F505AD31FDD937887229CCE53` | Current direct dependency input | Byte-unchanged; dependency versions are not product-version labels |

## Result

- Root files audited: **12/12**.
- Materially stale files in this correction: **2** (`.env.example`,
  `SECURITY.md`).
- Already-current root files deliberately preserved byte-for-byte: **10**.
- No candidate, HIL decision, accepted-pointer movement, deployment, or
  default-branch mutation is implied by this receipt.

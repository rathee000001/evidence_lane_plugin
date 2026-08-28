<!-- evidence-lane-public-docs-full-refresh: 3.0.0 / R265-current-route-v2 -->

# Third-party tool licenses and rights

<!-- EVIDENCE_LANE_CURRENT_BACKEND_START -->
## Current backend contract

This public document is refreshed from the same source graph used by the installable plugin package.

- Plugin package: `3.0.0+codex.20260828064341`.
- Native MCP: **91 actions** (**30 read / 61 write**).
- Native skills: **26 governed skills**; the separate command layer is absent.
- Hooks: **11 events / 44 ordered handler actions**.
- SDK: internal action SDK and outer routing SDK remain distinct; public action count **91**.
- ENV/UOP: separate executable authorities with **7 ENV members / 5 UOP members**.
- Runtime control lives in the hidden Codex plugin layer; Project/PV authority and task workspace remain separate user-selected identities.
- Public copy excludes internal receipts, task corrections, forensic reports, and historical execution documents.

Exact backend bindings:
  - `plugins/evidence-lane-plugin/.codex-plugin/plugin.json` — `42A726CD910A27EF9B8987907F02D127789857C8B04E1E214A91D1F74D151A4B`
  - `plugins/evidence-lane-plugin/schemas/public-action-schemas.v001.json` — `B571AF9EC31691C96DB0B3845ED0B7A6700D1C84A2578ABA9A2EA594982AF045`
  - `plugins/evidence-lane-plugin/skills/skill-surface-registry.v1.json` — `38B1F95B8160E037B43B209A6D6047BF8BCA4D2599182C2F20E4606B6CBDF3A5`
  - `plugins/evidence-lane-plugin/hooks/hooks.json` — `C37DB05DD4701087EAD0BD31203C843AAFA79ED39A081F2E9DFF313A77631EEF`
  - `plugins/evidence-lane-plugin/sdk/sdk-manifest.v1.json` — `5BD21AEB96D7E41209E3D059D8A5296D851BDED1D453D6EF486C0CD50D745245`
  - `plugins/evidence-lane-plugin/mcp/mcp-manifest.v1.json` — `E9E402C2F20B2BBE63B6BF91613B1C97E85E615F982D52CF6D020408251AFAFB`
  - `plugins/evidence-lane-plugin/env/authority-manifest.v1.json` — `E4F283EC16F86995E2937288DD8A8E5623007351CBB1CA3FD01FDA5C7363B6C1`
  - `plugins/evidence-lane-plugin/uop/authority-manifest.v1.json` — `BBA3CDAE9CC0FF981E5C6E19F83FBBCE6EB2ED8167CDBB2E9D1C557FA03CA57C`
  - `plugins/evidence-lane-plugin/toolchains/TOOLCHAIN_EXECUTION_MATRIX.md` — `E5379D7C4B17BC9293F332216581D60F88ADF73A4B7B361D84D09B47FC4EA66F`
<!-- EVIDENCE_LANE_CURRENT_BACKEND_END -->


This page covers license-bearing software and external services used by the
Evidence Lane source, package, tests, and documentation adapter. It does not
apply a third-party license to Evidence Lane's own schemas, lifecycle designs,
artwork, internal tools, or project artifacts.

## Packaged and direct dependencies

The exact pinned direct Python and public-adapter dependencies, versions, and
declared license metadata are recorded here and in the package lock/manifests.
Required license files distributed inside a dependency wheel or package remain
authoritative and must travel with any redistributed binary artifact.

The Windows x86-64 plugin package includes `ripgrep==15.2.0` under its upstream
MIT-or-Unlicense choice. The exact upstream texts are stored at
`../plugins/evidence-lane-plugin/toolchains/licenses/ripgrep-15.2.0/`, and the
binary identity is pinned by
`../plugins/evidence-lane-plugin/toolchains/search-tools.v1.json`.

`pypdfium2==5.12.1` declares BSD-3-Clause and Apache-2.0 coverage plus PDFium
and build-dependency notices. Any redistributed wheel/runtime must preserve the
license files included by that exact distribution. `PyMuPDF==1.28.2` is used by
the local document toolchain for high-fidelity extraction but remains an
AGPL-or-commercial-license-gated dependency. No proprietary binary publication
may bundle or provision it without an explicit compatible license decision and
the required source/notices; `pypdfium2`, `pypdf`, and `pdfplumber` remain the
permissive fallbacks when that gate is not satisfied.

The package-local notice is
[plugins/evidence-lane-plugin/THIRD_PARTY_NOTICES.md](../plugins/evidence-lane-plugin/THIRD_PARTY_NOTICES.md).

## Host tools not bundled by this repository

Git, Python, SQLite, Node.js, Mermaid CLI, Graphviz, Ruff, MyPy, Tesseract,
Poppler, Ghostscript, Chrome, Edge, and other host-resolved tools remain under
their respective upstream licenses and distribution terms. Listing a tool in
[Tools](TOOLS.md) records a supported role; it does not claim that its
binary or license text is redistributed by this repository. Any release that
starts bundling one must pin its exact version and hash, preserve its required
license text, and include it in the artifact SBOM.

## External services and trademarks

OpenAI, GitHub, Vercel, Devpost, Cloudflare, Google, and other external services
remain governed by their own service terms, privacy terms, acceptable-use
policies, and trademarks. A service connection grants no Evidence Lane source
rights, project authority, HIL approval, Git authorization, or publication
authority.

## Release requirement

Before commercial or binary publication, generate an SBOM for the exact release
artifact, audit the complete transitive and native-binary graph, and preserve
all required notices. The repository summaries are engineering records, not a
substitute for the authoritative upstream license texts or qualified legal
review.

# Third-party tool licenses and rights

This page covers license-bearing software and external services used by the
Evidence Lane source, package, tests, and documentation adapter. It does not
apply a third-party license to Evidence Lane's own schemas, lifecycle designs,
artwork, internal tools, or project artifacts.

## Packaged and direct dependencies

The exact pinned direct Python and public-adapter dependencies, versions, and
declared license metadata are recorded in the
[direct dependency license audit](DEPENDENCY_LICENSE_AUDIT.md). Required
license files distributed inside a dependency wheel or package remain
authoritative and must travel with any redistributed binary artifact.

The Windows x86-64 plugin package includes `ripgrep==15.2.0` under its upstream
MIT-or-Unlicense choice. The exact upstream texts are stored at
`../plugins/evidence-lane-plugin/toolchains/licenses/ripgrep-15.2.0/`, and the
binary identity is pinned by
`../plugins/evidence-lane-plugin/toolchains/search-tools.v1.json`.

`pypdfium2==5.12.1` declares BSD-3-Clause and Apache-2.0 coverage plus PDFium
and build-dependency notices. Any redistributed wheel/runtime must preserve the
license files included by that exact distribution. `PyMuPDF` is not an active
dependency and is not distributed by this source line.

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

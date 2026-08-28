# Third-party dependency notices

Audit date: **2026-08-15**

Evidence Lane's proprietary license applies only to Evidence Lane material. It
does not replace the licenses, notices, or trademarks of third-party packages.
The exact dependency versions are pinned in `pyproject.toml`,
`requirements.torch-cpu.lock.txt`, `requirements.lock.txt`,
`requirements.toolchain.lock.txt`, and `apps/evidence-lane-app/package.json`.

Direct Python runtime dependency metadata is permissive or Python Software
Foundation-family: Apache-2.0, BSD-3-Clause, MIT, MIT-CMU, PSF/PSFL, or an
explicit combination of those terms. Direct public-adapter dependencies report
MIT, except TypeScript, which reports Apache-2.0. This summary is not a
substitute for the license files distributed by each package.

PDF page rendering uses `pypdfium2==5.12.1`. Its installed Windows wheel carries
BSD-3-Clause and Apache-2.0 texts plus PDFium and build-dependency notices. Any
redistributed wheel or binary runtime must preserve those exact bundled files.
See the [pypdfium2 project](https://github.com/pypdfium2-team/pypdfium2) and
[published package metadata](https://pypi.org/project/pypdfium2/).

`PyMuPDF==1.28.2` is an active local document-toolchain dependency for
high-fidelity extraction. Its AGPL-or-commercial licensing model is an explicit
release gate: no proprietary binary publication may bundle or provision it
without a compatible license decision and the required source/notices.
Permissive PDF fallbacks remain available when that gate is not satisfied.

The Windows x86-64 Codex package includes `ripgrep==15.2.0` under its upstream
MIT-or-Unlicense choice. Its exact license texts are preserved under
`toolchains/licenses/`; the executable bytes, version, size, and SHA-256
identity are sealed by `toolchains/search-tools.v1.json`. Ripgrep is a bounded
pre-index file/content helper, not the SQLite FTS5 authority or a lifecycle
authority. A deterministic Python fallback remains packaged for unsupported,
missing, or identity-mismatched binaries.

The source package excludes virtual environments, installed wheels,
`node_modules`, local environment files, caches, source maps, TypeScript build
state, and generated 3D assets. Before commercial or binary publication,
generate an SBOM for the exact release artifact, audit its complete transitive
dependency and native-binary graph, and preserve every required notice.

The ordered repository-facing summary is
[`THIRD_PARTY_LICENSES.md`](../../docs/THIRD_PARTY_LICENSES.md). The exact declared
direct-version table is
[`docs/THIRD_PARTY_LICENSES.md`](../../docs/THIRD_PARTY_LICENSES.md),
and the lane-to-tool declaration is
[`docs/TOOLS.md`](../../docs/TOOLS.md). Internal Evidence Lane components shown
on the Tools page do not acquire a separate third-party license merely because
they have a visible tool label.

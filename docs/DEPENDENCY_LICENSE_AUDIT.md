# Direct dependency license audit

Audit date: **2026-08-09**
Release line: **Evidence Lane 1.5.0 source / governed v1.4 correction base**

This is an engineering distribution check, not legal advice and not a complete
transitive-license clearance. It records the direct dependencies declared by
the root package, installed plugin package, and public Next.js adapter. The
authoritative license texts remain the ones distributed by each dependency.

## Corrective finding

`PyMuPDF==1.28.0` was removed from the active dependency manifests and active
source. PyMuPDF is offered under the GNU AGPL and a separate commercial license;
shipping it in a proprietary Evidence Lane distribution would therefore require
either an applicable commercial license or full compliance with the governing
open-source terms. Neither may be inferred from a passing local test.

PDF page rendering now uses pinned `pypdfium2==5.12.1`; native PDF text and
embedded-image extraction use pinned `pypdf==6.14.2`, with the existing OCR
fallbacks. Derived public lane PNGs are rasterized from stable SVG bytes by the
already-installed Chromium browser, so those published fixtures no longer carry
PyMuPDF provenance.

The pypdfium2 project declares BSD-3-Clause and Apache-2.0 coverage plus
dependency licenses. The installed Windows wheel includes its own license files
and PDFium build notices. Any future wheel, binary installer, or redistributed
runtime must preserve those exact bundled notices; copying only this summary is
not sufficient. See the [pypdfium2 project](https://github.com/pypdfium2-team/pypdfium2)
and [published package metadata](https://pypi.org/project/pypdfium2/).

## Direct Python runtime dependencies

The values below were checked against installed distribution metadata for the
exact pinned versions. `PSFL`/`PSF` means a Python Software Foundation family
license as declared by the package.

| Dependency | Version | Declared license metadata |
| --- | ---: | --- |
| cryptography | 48.0.1 | Apache-2.0 OR BSD-3-Clause |
| defusedxml | 0.7.1 | PSFL |
| httpx | 0.28.1 | BSD-3-Clause |
| mcp | 1.28.1 | MIT |
| pydantic | 2.13.4 | MIT |
| onnxruntime | 1.28.0 | MIT |
| openpyxl | 3.1.5 | MIT |
| pandas | 3.0.5 | BSD-3-Clause |
| pdfplumber | 0.11.10 | MIT |
| Pillow | 12.3.0 | MIT-CMU |
| pyarrow | 25.0.0 | Apache-2.0 |
| PyJWT | 2.13.0 | MIT |
| pypdf | 6.14.2 | BSD-3-Clause |
| pypdfium2 | 5.12.1 | BSD-3-Clause, Apache-2.0, and dependency licenses |
| pytesseract | 0.3.13 | Apache-2.0 |
| pywin32 | 312 | PSF |
| python-calamine | 0.8.2 | MIT |
| rapidocr | 3.9.2 | Apache-2.0 |

The optional direct development/RAG dependencies are `pytest==9.1.1` (MIT)
and `llama-index-core==0.14.23` (MIT). Build-system dependencies are
`setuptools==83.0.0` (MIT) and `wheel==0.46.3` (MIT). External executables such
as Tesseract, Chrome, Edge, and Mermaid CLI are not embedded in the source
package by this repository; any later redistribution must audit their exact
distribution separately.

## Direct public adapter dependencies

Installed metadata reports MIT for `framer-motion@12.38.0`, `next@16.1.5`,
`react@19.2.4`, `react-dom@19.2.4`, `three@0.185.1`, `@types/node@24.10.1`,
`@types/react@19.2.7`, `@types/react-dom@19.2.3`, and `@types/three@0.185.3`.
`typescript@5.9.3` reports Apache-2.0.

## Distribution boundary and remaining work

The governed source package excludes virtual environments, installed wheels,
`node_modules`, local environment files, caches, source maps, TypeScript build
state, and generated 3D assets. That reduces accidental redistribution; it does
not erase license obligations for dependencies later installed or bundled by a
release mechanism.

Before any commercial or binary publication, generate an SBOM for the exact
release artifact, inspect the complete transitive graph and bundled native
binaries, preserve all required notices, and obtain qualified legal review when
the distribution model requires it.

**Verdict: FIX-THEN-PURSUE (high confidence).** The identified direct AGPL or
commercial-license risk has been removed from active source and manifests, so
local correction work should continue. Publication should remain blocked until
the exact final artifact passes transitive/SBOM review and any needed legal
review. Evidence of an applicable PyMuPDF commercial license, a deliberate
AGPL-compliant distribution model, or a materially different final dependency
graph would change this assessment.

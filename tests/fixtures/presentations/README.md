# Independent presentation fixtures

These unmodified binary fixtures come from the python-pptx and Apache POI test
corpora. Each directory retains its upstream license (and NOTICE where
provided), immutable commit, original path, source URL, byte count and SHA-256
in `manifest.json`. They are test data and are not shipped as executable plugin
assets. No upstream fixture-generating code or embedded macro is executed.

The native intake tests compare content, slide order, local geometry, notes,
table cells, cached chart values and media hashes with an independent reader.
POTX and PPSX use a separate OPC/XML read because the selected reader's public
presentation factory accepts PPTX/PPTM. Exact edits are checked for unchanged
package parts and preserved styles. Runtime tests use the isolated, verified
shared Impress installation selected by `EVI_DOCUMENT_QUALIFICATION_ASSETS`.

The corpus includes deliberately unusual shapes, cropping and sparse slides.
Successful rendering does not imply Microsoft PowerPoint layout equivalence.
Macros, embedded OLE objects and embedded chart workbooks remain preserved
input data and are excluded from rendering by the current format contract.

"""Fixed Poppler commands over immutable PDF bytes inside the owned PDF worker."""

import re

from .installation_layout import studio_installation
from .pdf_parsers import digest, fail
from .shared_native_tools import NativeInvocationRequest, run_native_tool
from .shared_tool_assets import resolve_shared_asset


def poppler_text(content, page_count):
    _, asset = resolve_shared_asset("poppler_runtime")
    responses = []
    for tool, arguments in (
        ("poppler_pdfinfo", ["-"]),
        ("poppler_pdftotext", ["-layout", "-enc", "UTF-8", "-", "-"]),
    ):
        response = run_native_tool(
            NativeInvocationRequest(
                tool_id=tool,
                arguments=arguments,
                input_bytes=content,
                timeout_seconds=60,
                max_output_bytes=8_388_608,
                host_profile="CODEX_DESKTOP",
            ),
            runtime_root=studio_installation().active_root,
        )
        if response["status"] != "PASS":
            fail("POPLER_EXTRACTION_FAILED")
        responses.append(response)
    found = re.search(r"(?m)^Pages:\s+(\d+)\s*$", responses[0]["stdout"])
    pages = responses[1]["stdout"].split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    if found is None or int(found[1]) != page_count or len(pages) != page_count:
        fail("POPLER_PAGE_BINDING")
    if resolve_shared_asset("poppler_runtime")[1]["files_sha256"] != asset["files_sha256"]:
        fail("POPLER_RUNTIME_CHANGED")
    return pages, {
        "runtime_sha256": asset["files_sha256"],
        "tools": [
            {
                "tool_id": tool,
                "version": response["version"],
                "executable_sha256": response["executable_sha256"],
                "output_sha256": digest(response["stdout"].encode()),
            }
            for tool, response in zip(
                ["poppler_pdfinfo", "poppler_pdftotext"], responses, strict=True
            )
        ],
    }

"""Deterministic eighteen-lane SQLite/MMD/DOT build and incremental Refresh."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import math
import mimetypes
import posixpath
import re
import shutil
import sqlite3
import zipfile
from collections import Counter
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any, ClassVar

from defusedxml import ElementTree

from .git_history import (
    create_git_history_schema,
    git_history_signature,
    index_git_history,
)
from .git_optional import probe_git_arm
from .hashing import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json_bytes,
    sha256_bytes,
    sha256_file,
)
from .ingest import extract_code_lane_facts, governed_source_files
from .lanes import (
    CANONICAL_LANE_IDS,
    LANE_REGISTRY,
    PRIMARY_CODE_LANES,
    LaneDefinition,
    catalog,
    route_batch,
    route_source,
)
from .redaction import redact_text
from .timeutil import utc_now
from .topology_reconciliation import (
    reconcile_bundle_topology,
    reconcile_lane_topology,
)

LANE_SCHEMA_VERSION = "evidence-lane.universal-lane.v2"
LEGACY_LANE_BUNDLE_SCHEMA = "evidence-lane.universal-lane-bundle.v1"
LANE_BUNDLE_SCHEMA = "evidence-lane.universal-lane-bundle.v2"
MAX_EXTRACT_BYTES = 64 * 1024 * 1024
MAX_PDF_PAGES = 500
MAX_ROWS_PER_TAB = 5000
CHUNK_CHARS = 6000
CHUNK_OVERLAP = 500
TFIDF_TERMS_PER_CHUNK = 256
MAX_PARALLEL_LANE_WORKERS = 8
_TOKEN_RE = re.compile(r"[\w][\w.-]{1,63}", flags=re.UNICODE)
_XML_TEXT_TAG = re.compile(r"}t$")
_RAPIDOCR_CALL_LOCK = Lock()


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _capability_rows(lane: LaneDefinition) -> list[dict[str, str]]:
    rows = [
        {
            "capability": "exact_source_bytes",
            "state": "ACTIVE",
            "tool": "python pathlib/hashlib",
            "detail": "Every routed source is hash-bound even when parsing is unavailable.",
        },
        {
            "capability": "fts5_bm25",
            "state": "ACTIVE",
            "tool": "sqlite3 FTS5",
            "detail": "Unicode FTS with explicit BM25 ranking.",
        },
        {
            "capability": "tfidf",
            "state": "ACTIVE",
            "tool": "deterministic Python term statistics",
            "detail": "Materialized TF, document frequency, IDF, and TF-IDF values.",
        },
        {
            "capability": "mermaid_source",
            "state": "ACTIVE",
            "tool": "deterministic Mermaid emitter",
            "detail": "Authoritative lane topology is emitted as Mermaid source.",
        },
        {
            "capability": "dot_source",
            "state": "ACTIVE",
            "tool": "deterministic DOT emitter",
            "detail": "Authoritative lane topology is emitted as Graphviz DOT source.",
        },
        {
            "capability": "mermaid_render_mmdc",
            "state": "ACTIVE" if shutil.which("mmdc") else "UNAVAILABLE",
            "tool": "mmdc",
            "detail": "Optional derived render; Mermaid source remains authoritative.",
        },
        {
            "capability": "graphviz_render_dot",
            "state": "ACTIVE" if shutil.which("dot") else "UNAVAILABLE",
            "tool": "dot",
            "detail": "Optional derived render; DOT source remains authoritative.",
        },
    ]
    if lane.canonical_lane_id == "pdf_ocr":
        for capability, module in (
            ("pdf_native_text_pymupdf", "fitz"),
            ("pdf_native_text_pypdf", "pypdf"),
            ("pdf_structural_pdfplumber", "pdfplumber"),
            ("ocr_pytesseract", "pytesseract"),
            ("ocr_rapidocr", "rapidocr"),
            ("ocr_onnxruntime", "onnxruntime"),
            ("document_parser_docling", "docling"),
            ("image_pillow", "PIL"),
            ("image_opencv", "cv2"),
        ):
            rows.append(
                {
                    "capability": capability,
                    "state": "ACTIVE" if _module_available(module) else "UNAVAILABLE",
                    "tool": module,
                    "detail": "Optional V1/V3 extractor; absence is fail-visible.",
                }
            )
        rows.append(
            {
                "capability": "ocr_tesseract_binary",
                "state": "ACTIVE" if shutil.which("tesseract") else "UNAVAILABLE",
                "tool": "tesseract",
                "detail": "Required by pytesseract for local OCR.",
            }
        )
        for capability, commands in (
            ("pdf_poppler_pdftotext", ("pdftotext",)),
            ("pdf_poppler_pdfinfo", ("pdfinfo",)),
            ("pdf_ghostscript", ("gs", "gswin64c", "gswin32c")),
        ):
            command = next((item for item in commands if shutil.which(item)), None)
            rows.append(
                {
                    "capability": capability,
                    "state": "ACTIVE" if command else "UNAVAILABLE",
                    "tool": command or commands[0],
                    "detail": "V1/V3 local extraction tool; absence is fail-visible.",
                }
            )
    elif lane.canonical_lane_id == "images_ocr":
        for capability, module in (
            ("image_metadata", "PIL"),
            ("ocr_pytesseract", "pytesseract"),
            ("ocr_rapidocr", "rapidocr"),
            ("ocr_onnxruntime", "onnxruntime"),
            ("image_opencv", "cv2"),
        ):
            rows.append(
                {
                    "capability": capability,
                    "state": "ACTIVE" if _module_available(module) else "UNAVAILABLE",
                    "tool": module,
                    "detail": "Optional local image/OCR extractor.",
                }
            )
        rows.append(
            {
                "capability": "ocr_tesseract_binary",
                "state": "ACTIVE" if shutil.which("tesseract") else "UNAVAILABLE",
                "tool": "tesseract",
                "detail": "Required only for the pytesseract OCR route.",
            }
        )
    elif lane.canonical_lane_id == "data_excel":
        rows.extend(
            [
                {
                    "capability": "xlsx_openxml",
                    "state": "ACTIVE",
                    "tool": "zipfile+xml.etree",
                    "detail": "Workbook, sheet, cell, formula, and relationship extraction.",
                },
                {
                    "capability": "csv_tsv",
                    "state": "ACTIVE",
                    "tool": "python csv",
                    "detail": "Bounded structural row extraction.",
                },
                {
                    "capability": "json_jsonl",
                    "state": "ACTIVE",
                    "tool": "python json",
                    "detail": "Bounded object/list shape and row-sample extraction.",
                },
            ]
        )
        for capability, module in (
            ("excel_openpyxl", "openpyxl"),
            ("excel_pandas", "pandas"),
            ("parquet_pyarrow", "pyarrow"),
            ("excel_calamine", "python_calamine"),
        ):
            rows.append(
                {
                    "capability": capability,
                    "state": "ACTIVE" if _module_available(module) else "UNAVAILABLE",
                    "tool": module,
                    "detail": "Optional higher-fidelity tabular extractor.",
                }
            )
    elif lane.canonical_lane_id == "docs":
        rows.append(
            {
                "capability": "docx_openxml",
                "state": "ACTIVE",
                "tool": "zipfile+xml.etree",
                "detail": "Hierarchy-preserving DOCX text/table extraction.",
            }
        )
    elif lane.canonical_lane_id == "ppt":
        rows.append(
            {
                "capability": "pptx_openxml",
                "state": "ACTIVE",
                "tool": "zipfile+xml.etree",
                "detail": "Slide/notes/shape text and relationship extraction.",
            }
        )
    elif lane.canonical_lane_id in {"sqlite_brain", "brain_loader"}:
        rows.append(
            {
                "capability": "sqlite_read_only_inspection",
                "state": "ACTIVE",
                "tool": "sqlite3 immutable URI",
                "detail": "Schema, integrity, foreign keys, FTS objects; imported SQL is never run.",
            }
        )
    return rows


def _tool_identity(lane: LaneDefinition) -> dict[str, Any]:
    capabilities = _capability_rows(lane)
    payload = {
        "lane": lane.as_dict(),
        "capabilities": capabilities,
        "lane_schema_version": LANE_SCHEMA_VERSION,
    }
    payload["sha256"] = sha256_bytes(canonical_json_bytes(payload))
    return payload


def _decode_text(data: bytes) -> tuple[str | None, str | None]:
    if not data:
        return "", "utf-8"
    if b"\x00" in data[:8192]:
        for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
            try:
                return data.decode(encoding), encoding
            except UnicodeError:
                continue
        return None, None
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeError:
            continue
    return None, None


def _xml_text(root: ElementTree.Element) -> str:
    values = [
        str(node.text)
        for node in root.iter()
        if node.text
        and (_XML_TEXT_TAG.search(node.tag) or node.tag.endswith("}instrText"))
    ]
    return "\n".join(values)


def _fact(kind: str, locator: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "locator": locator, "payload": payload}


def _append_fact_once(
    facts: list[dict[str, Any]],
    kind: str,
    locator: str,
    payload: dict[str, Any],
) -> None:
    if any(item["kind"] == kind and item["locator"] == locator for item in facts):
        return
    facts.append(_fact(kind, locator, payload))


_SOURCE_FACT_KIND = {
    "discussion": "discussion_source",
    "analysis": "analysis_source",
    "plan": "plan_source",
    "mode": "mode_source",
    "docs": "doc_file",
    "data_excel": "data_source",
    "ppt": "ppt_file",
    "pdf_ocr": "pdf_file",
    "images_ocr": "image_file",
    "artifacts": "project_artifact",
    "custom": "custom_source",
    "brain_loader": "brain_loader_source",
    "research": "research_source",
    "project_engulf": "project_engulf_source",
    "sqlite_brain": "loaded_sqlite_brain_source",
}

_STRUCTURE_FACT_KIND = {
    "docs": "source_structure_signature",
    "data_excel": "data_structure_signature",
    "ppt": "ppt_structure_signature",
    "pdf_ocr": "pdf_structure_signature",
}

_PREFIX_FACT_KIND = {
    "discussion": {
        "decision": "discussion_decision",
        "delta": "discussion_delta",
        "next": "discussion_next_action",
        "next_action": "discussion_next_action",
        "gate": "discussion_hard_gate",
        "artifact": "discussion_artifact_reference",
    },
    "analysis": {
        "claim": "analysis_claim",
        "evidence": "analysis_evidence",
        "supporting_evidence": "analysis_supporting_evidence",
        "risk": "analysis_risk",
        "alternative": "analysis_alternative",
        "question": "analysis_open_question",
        "open_question": "analysis_open_question",
        "accepted_decision": "analysis_accepted_decision",
        "blocked": "analysis_blocked_item",
    },
    "plan": {
        "phase": "plan_phase",
        "milestone": "plan_milestone",
        "task": "plan_task",
        "owner": "plan_owner",
        "status": "plan_status",
        "depends": "plan_dependency",
        "dependency": "plan_dependency",
        "blocker": "plan_blocker",
        "next": "plan_next_action",
        "next_action": "plan_next_action",
        "acceptance": "plan_acceptance_criteria",
        "acceptance_criteria": "plan_acceptance_criteria",
    },
    "mode": {
        "scope": "mode_scope",
        "trigger": "mode_trigger",
        "rule": "mode_rule",
        "gate": "mode_gate",
        "allow": "mode_allowed_action",
        "allowed": "mode_allowed_action",
        "block": "mode_blocked_action",
        "blocked": "mode_blocked_action",
        "response": "mode_response_template",
        "priority": "mode_priority",
        "supersedes": "mode_supersede_ledger",
    },
    "research": {
        "question": "research_question",
        "hypothesis": "research_hypothesis",
        "method": "research_method",
        "evidence": "research_evidence",
        "finding": "research_finding",
        "limitation": "research_limitation",
        "citation": "research_citation",
        "open_question": "research_open_question",
    },
    "custom": {
        "item": "custom_item",
        "evidence": "custom_evidence",
        "decision": "custom_decision",
        "next": "custom_next_action",
        "next_action": "custom_next_action",
    },
}


def _semantic_lane_facts(
    lane: LaneDefinition,
    text: str,
    *,
    relative_path: str,
) -> list[dict[str, Any]]:
    mapping = _PREFIX_FACT_KIND.get(lane.canonical_lane_id, {})
    facts: list[dict[str, Any]] = []
    lines = text.splitlines()
    if lane.canonical_lane_id == "discussion":
        for index, line in enumerate(lines[:500], start=1):
            if line.strip():
                facts.append(
                    _fact(
                        "discussion_item",
                        f"{relative_path}:line:{index}",
                        {
                            "text": line.strip(),
                            "line": index,
                            "extractor": "bounded_visible_line_v1",
                        },
                    )
                )
    for index, line in enumerate(lines[:2000], start=1):
        stripped = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s+", "", line).strip()
        match = re.match(r"^([A-Za-z][A-Za-z0-9 _/-]{0,48}):\s*(.+)$", stripped)
        if not match:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", match.group(1).lower()).strip("_")
        kind = mapping.get(key)
        if not kind:
            continue
        facts.append(
            _fact(
                kind,
                f"{relative_path}:line:{index}",
                {
                    "text": match.group(2).strip(),
                    "label": match.group(1),
                    "line": index,
                    "extractor": "deterministic_prefix_v1",
                },
            )
        )
    return facts


def _document_text_facts(text: str, relative_path: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    paragraphs = [
        block.strip() for block in re.split(r"(?:\r?\n){2,}", text) if block.strip()
    ][:1000]
    for index, paragraph in enumerate(paragraphs, start=1):
        first_line = paragraph.splitlines()[0].strip()
        markdown = re.match(r"^(#{1,6})\s+(.+)$", first_line)
        if markdown:
            facts.append(
                _fact(
                    "doc_heading",
                    f"{relative_path}:heading:{index}",
                    {
                        "level": len(markdown.group(1)),
                        "text": markdown.group(2).strip(),
                        "extractor": "markdown_heading_v1",
                    },
                )
            )
        facts.append(
            _fact(
                "doc_paragraph",
                f"{relative_path}:paragraph:{index}",
                {
                    "text": paragraph,
                    "chars": len(paragraph),
                    "extractor": "bounded_paragraph_v1",
                },
            )
        )
    facts.append(
        _fact(
            "doc_structure",
            relative_path,
            {
                "paragraphs": len(paragraphs),
                "headings": sum(item["kind"] == "doc_heading" for item in facts),
                "extractor": "text_hierarchy_v1",
            },
        )
    )
    return facts


def _finalize_extraction(
    lane: LaneDefinition,
    relative_path: str,
    documents: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    parser_state: str,
    encoding: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str | None]:
    text = "\n\n".join(
        str(document.get("text") or "")
        for document in documents
        if str(document.get("text") or "")
    )
    source_kind = _SOURCE_FACT_KIND.get(lane.canonical_lane_id)
    if source_kind:
        _append_fact_once(
            facts,
            source_kind,
            relative_path,
            {
                "path": relative_path,
                "extension": Path(relative_path).suffix.lower(),
                "parser_state": parser_state,
                "documents": len(documents),
                "facts": len(facts),
                "text_chars": len(text),
            },
        )
    structure_kind = _STRUCTURE_FACT_KIND.get(lane.canonical_lane_id)
    if structure_kind:
        _append_fact_once(
            facts,
            structure_kind,
            relative_path,
            {
                "parser_state": parser_state,
                "document_count": len(documents),
                "fact_kinds": sorted({str(item["kind"]) for item in facts}),
                "text_sha256": sha256_bytes(text.encode("utf-8")),
            },
        )
    if text:
        facts.extend(_semantic_lane_facts(lane, text, relative_path=relative_path))
    if lane.canonical_lane_id == "docs" and not any(
        item["kind"] == "doc_paragraph" for item in facts
    ):
        facts.extend(_document_text_facts(text, relative_path))
    if lane.canonical_lane_id == "artifacts":
        _append_fact_once(
            facts,
            "artifact_metadata",
            relative_path,
            {
                "extension": Path(relative_path).suffix.lower(),
                "parser_state": parser_state,
                "text_chars": len(text),
            },
        )
        for index, document in enumerate(documents[:200], start=1):
            facts.append(
                _fact(
                    "artifact_text_extract",
                    f"{relative_path}:extract:{index}",
                    {
                        "source_locator": document.get("locator"),
                        "text_sha256": sha256_bytes(
                            str(document.get("text") or "").encode("utf-8")
                        ),
                    },
                )
            )
        if not documents:
            facts.append(
                _fact(
                    "artifact_review_required",
                    relative_path,
                    {
                        "reason": "NO_TEXT_EXTRACT_AVAILABLE",
                        "exact_bytes_preserved": True,
                    },
                )
            )
    if lane.canonical_lane_id == "custom":
        _append_fact_once(
            facts,
            "custom_item",
            relative_path,
            {
                "parser_state": parser_state,
                "text_chars": len(text),
                "exact_bytes_preserved": True,
            },
        )
        if text:
            _append_fact_once(
                facts,
                "custom_evidence",
                relative_path,
                {"text_sha256": sha256_bytes(text.encode("utf-8"))},
            )
    return documents, facts, parser_state, encoding


def _extract_docx(data: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for name in sorted(archive.namelist()):
            if not (
                name == "word/document.xml"
                or name.startswith(("word/header", "word/footer", "word/footnotes"))
            ):
                continue
            root = ElementTree.fromstring(archive.read(name))
            paragraph_texts: list[str] = []
            paragraph_count = 0
            heading_count = 0
            for paragraph in (node for node in root.iter() if node.tag.endswith("}p")):
                text = _xml_text(paragraph).strip()
                if not text:
                    continue
                paragraph_count += 1
                paragraph_texts.append(text)
                style = ""
                for node in paragraph.iter():
                    if not node.tag.endswith("}pStyle"):
                        continue
                    style = next(
                        (
                            str(value)
                            for key, value in node.attrib.items()
                            if key.endswith("}val")
                        ),
                        "",
                    )
                    break
                heading_match = re.match(r"(?i)^heading\s*([1-6])$", style)
                if heading_match:
                    heading_count += 1
                    facts.append(
                        _fact(
                            "doc_heading",
                            f"{name}:heading:{heading_count}",
                            {
                                "level": int(heading_match.group(1)),
                                "style": style,
                                "text": text,
                            },
                        )
                    )
                else:
                    facts.append(
                        _fact(
                            "doc_paragraph",
                            f"{name}:paragraph:{paragraph_count}",
                            {
                                "style": style or None,
                                "text": text,
                            },
                        )
                    )
            table_count = 0
            for table in (node for node in root.iter() if node.tag.endswith("}tbl")):
                table_count += 1
                rows: list[list[str]] = []
                for row in (node for node in table if node.tag.endswith("}tr")):
                    cells = [
                        _xml_text(cell).strip()
                        for cell in row
                        if cell.tag.endswith("}tc")
                    ]
                    if cells:
                        rows.append(cells)
                facts.append(
                    _fact(
                        "doc_table_extract",
                        f"{name}:table:{table_count}",
                        {
                            "rows": rows[:200],
                            "row_count": len(rows),
                            "truncated": len(rows) > 200,
                        },
                    )
                )
                paragraph_texts.extend(
                    " | ".join(cell for cell in row) for row in rows[:200]
                )
            image_count = 0
            for node in root.iter():
                if not node.tag.endswith("}blip"):
                    continue
                relationship_id = next(
                    (
                        str(value)
                        for key, value in node.attrib.items()
                        if key.endswith(("}embed", "}link"))
                    ),
                    "",
                )
                image_count += 1
                facts.append(
                    _fact(
                        "doc_image_reference",
                        f"{name}:image:{image_count}",
                        {"relationship_id": relationship_id},
                    )
                )
            text = "\n\n".join(paragraph_texts)
            if text:
                documents.append({"locator": name, "text": text, "metadata": {}})
            facts.append(
                _fact(
                    "doc_structure",
                    name,
                    {
                        "paragraphs": paragraph_count,
                        "headings": heading_count,
                        "tables": table_count,
                        "images": image_count,
                    },
                )
            )
    return documents, facts


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    values: list[str] = []
    for item in root:
        values.append(
            "".join(node.text or "" for node in item.iter() if node.tag.endswith("}t"))
        )
    return values


def _extract_xlsx(data: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        strings = _shared_strings(archive)
        sheet_members = sorted(
            name
            for name in archive.namelist()
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
        )
        sheet_titles: dict[str, str] = {}
        workbook_sheets: list[dict[str, str]] = []
        if (
            "xl/workbook.xml" in archive.namelist()
            and "xl/_rels/workbook.xml.rels" in archive.namelist()
        ):
            relationships = ElementTree.fromstring(
                archive.read("xl/_rels/workbook.xml.rels")
            )
            targets = {
                node.attrib.get("Id", ""): posixpath.normpath(
                    posixpath.join("xl", node.attrib.get("Target", ""))
                )
                for node in relationships
            }
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            for sheet in (
                node for node in workbook.iter() if node.tag.endswith("}sheet")
            ):
                relationship_id = next(
                    (
                        value
                        for key, value in sheet.attrib.items()
                        if key.endswith("}id")
                    ),
                    "",
                )
                member = targets.get(relationship_id, "")
                title = sheet.attrib.get("name", member or relationship_id)
                if member:
                    sheet_titles[member] = title
                workbook_sheets.append(
                    {
                        "title": title,
                        "sheet_id": sheet.attrib.get("sheetId", ""),
                        "member": member,
                    }
                )
            defined_names = [
                {
                    "name": node.attrib.get("name", ""),
                    "formula": node.text or "",
                }
                for node in workbook.iter()
                if node.tag.endswith("}definedName")
            ]
            facts.append(
                {
                    "kind": "sheet_workbook",
                    "locator": "xl/workbook.xml",
                    "payload": {
                        "sheets": workbook_sheets,
                        "defined_names": defined_names,
                    },
                }
            )
        for sheet_member in sheet_members:
            sheet_title = sheet_titles.get(sheet_member, sheet_member)
            root = ElementTree.fromstring(archive.read(sheet_member))
            rows: list[str] = []
            formulas = 0
            cell_count = 0
            cell_refs: list[str] = []
            cell_samples: list[dict[str, Any]] = []
            for row_index, row in enumerate(
                (node for node in root.iter() if node.tag.endswith("}row")), start=1
            ):
                if row_index > MAX_ROWS_PER_TAB:
                    break
                cells: list[str] = []
                for cell in (node for node in row if node.tag.endswith("}c")):
                    cell_count += 1
                    ref = cell.attrib.get("r", "")
                    if ref:
                        cell_refs.append(ref)
                    cell_type = cell.attrib.get("t")
                    formula_node = next(
                        (node for node in cell if node.tag.endswith("}f")), None
                    )
                    value_node = next(
                        (node for node in cell if node.tag.endswith("}v")), None
                    )
                    if formula_node is not None:
                        formulas += 1
                        formula = formula_node.text or ""
                        value = f"={formula}"
                        facts.append(
                            {
                                "kind": "sheet_formula",
                                "locator": f"{sheet_title}!{ref}",
                                "payload": {
                                    "sheet": sheet_title,
                                    "cell": ref,
                                    "formula": formula,
                                    "cached_value": (
                                        value_node.text
                                        if value_node is not None
                                        else None
                                    ),
                                },
                            }
                        )
                        references = sorted(
                            {
                                (
                                    match.group("sheet")
                                    or match.group("quoted")
                                    or sheet_title,
                                    f"{match.group('column')}{match.group('row')}",
                                )
                                for match in re.finditer(
                                    r"(?:(?:'(?P<quoted>[^']+)'|"
                                    r"(?P<sheet>[A-Za-z_][A-Za-z0-9_. ]*))!)?"
                                    r"\$?(?P<column>[A-Z]{1,3})\$?"
                                    r"(?P<row>[1-9][0-9]*)",
                                    formula,
                                )
                            }
                        )
                        for target_sheet, target_cell in references:
                            facts.append(
                                {
                                    "kind": "sheet_formula_dependency_edge",
                                    "locator": f"{sheet_title}!{ref}",
                                    "payload": {
                                        "from_sheet": sheet_title,
                                        "from_cell": ref,
                                        "to_sheet": target_sheet.strip("'"),
                                        "to_cell": target_cell,
                                    },
                                }
                            )
                    else:
                        value = value_node.text or "" if value_node is not None else ""
                        if cell_type == "s" and value and value.isdigit():
                            index = int(value)
                            value = strings[index] if index < len(strings) else value
                    if len(cell_samples) < 2000:
                        cell_samples.append(
                            {
                                "sheet": sheet_title,
                                "cell": ref,
                                "value": value,
                                "cell_type": cell_type,
                                "formula": (
                                    formula_node.text or ""
                                    if formula_node is not None
                                    else None
                                ),
                                "cached_value": (
                                    value_node.text
                                    if formula_node is not None
                                    and value_node is not None
                                    else None
                                ),
                            }
                        )
                    cells.append(f"{ref}={value}")
                if cells:
                    rows.append(" | ".join(cells))
            text = "\n".join(rows)
            if text:
                documents.append(
                    {
                        "locator": sheet_title,
                        "text": text,
                        "metadata": {"bounded_rows": len(rows)},
                    }
                )
            facts.append(
                {
                    "kind": "sheet_tab",
                    "locator": sheet_title,
                    "payload": {
                        "member": sheet_member,
                        "bounded_rows": len(rows),
                        "cells": cell_count,
                        "formulas": formulas,
                        "row_limit": MAX_ROWS_PER_TAB,
                    },
                }
            )
            dimension = next(
                (
                    node.attrib.get("ref", "")
                    for node in root.iter()
                    if node.tag.endswith("}dimension")
                ),
                "",
            )
            if not dimension and cell_refs:
                dimension = (
                    cell_refs[0]
                    if len(cell_refs) == 1
                    else f"{cell_refs[0]}:{cell_refs[-1]}"
                )
            facts.append(
                _fact(
                    "sheet_range",
                    sheet_title,
                    {
                        "range": dimension,
                        "sampled_cells": len(cell_samples),
                        "total_cells": cell_count,
                    },
                )
            )
            facts.extend(
                _fact(
                    "sheet_cell_sample",
                    f"{sheet_title}!{sample['cell']}",
                    sample,
                )
                for sample in cell_samples
            )
        for table_name in sorted(
            name
            for name in archive.namelist()
            if re.fullmatch(r"xl/tables/table\d+\.xml", name)
        ):
            table = ElementTree.fromstring(archive.read(table_name))
            facts.append(
                {
                    "kind": "sheet_table",
                    "locator": table_name,
                    "payload": {
                        "name": table.attrib.get("name"),
                        "display_name": table.attrib.get("displayName"),
                        "range": table.attrib.get("ref"),
                    },
                }
            )
        chart_names = sorted(
            name for name in archive.namelist() if name.startswith("xl/charts/")
        )
        for chart_name in chart_names:
            chart = ElementTree.fromstring(archive.read(chart_name))
            chart_types = sorted(
                {
                    node.tag.rsplit("}", 1)[-1]
                    for node in chart.iter()
                    if node.tag.rsplit("}", 1)[-1].endswith("Chart")
                }
            )
            text_nodes = [
                node.text or ""
                for node in chart.iter()
                if node.tag.endswith("}t") and node.text
            ]
            series_references = sorted(
                {
                    node.text or ""
                    for node in chart.iter()
                    if node.tag.endswith("}f") and node.text
                }
            )
            facts.append(
                {
                    "kind": "sheet_chart_metadata",
                    "locator": chart_name,
                    "payload": {
                        "member": chart_name,
                        "chart_types": chart_types,
                        "title_text": " ".join(text_nodes),
                        "series_references": series_references,
                    },
                }
            )
    return documents, facts


def _extract_delimited(
    data: bytes, suffix: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    text, encoding = _decode_text(data)
    if text is None:
        return [], [], None
    delimiter = "\t" if suffix == ".tsv" else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    bounded = rows[:MAX_ROWS_PER_TAB]
    normalized = "\n".join(" | ".join(cell for cell in row) for row in bounded)
    facts = [
        _fact(
            "tabular_structure",
            "table",
            {
                "encoding": encoding,
                "rows_total": len(rows),
                "rows_indexed": len(bounded),
                "columns_max": max((len(row) for row in bounded), default=0),
                "header": bounded[0] if bounded else [],
            },
        ),
        _fact(
            "csv_header",
            "row:1",
            {
                "columns": bounded[0] if bounded else [],
                "column_count": len(bounded[0]) if bounded else 0,
                "delimiter": "\\t" if suffix == ".tsv" else ",",
            },
        ),
    ]
    facts.extend(
        _fact(
            "csv_row_sample",
            f"row:{row_index}",
            {
                "values": row,
                "column_count": len(row),
            },
        )
        for row_index, row in enumerate(bounded[1:201], start=2)
    )
    return (
        (
            [{"locator": "table", "text": normalized, "metadata": {}}]
            if normalized
            else []
        ),
        facts,
        encoding,
    )


def _bounded_json_value(value: Any, *, max_chars: int = 8192) -> dict[str, Any]:
    serialized = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    payload: dict[str, Any] = {
        "value_type": type(value).__name__,
        "canonical_sha256": sha256_bytes(serialized.encode("utf-8")),
        "canonical_chars": len(serialized),
    }
    if len(serialized) <= max_chars:
        payload["value"] = value
    else:
        payload["preview"] = serialized[:max_chars]
        payload["truncated"] = True
    return payload


def _json_shape(value: Any, *, depth: int = 0) -> dict[str, Any]:
    if depth >= 4:
        return {"type": type(value).__name__, "depth_limited": True}
    if isinstance(value, dict):
        keys = sorted(str(key) for key in value)[:200]
        return {
            "type": "object",
            "keys": keys,
            "key_count": len(value),
            "children": {
                key: _json_shape(value[key], depth=depth + 1)
                for key in keys[:50]
                if key in value
            },
            "truncated": len(value) > 200,
        }
    if isinstance(value, list):
        types = sorted({type(item).__name__ for item in value[:1000]})
        return {
            "type": "array",
            "length": len(value),
            "item_types": types,
            "sample_shape": (_json_shape(value[0], depth=depth + 1) if value else None),
        }
    return {"type": type(value).__name__}


def _extract_json(
    data: bytes,
    suffix: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    text, encoding = _decode_text(data)
    if text is None:
        return [], [], None
    if suffix == ".jsonl":
        records = [json.loads(line) for line in text.splitlines() if line.strip()][
            :MAX_ROWS_PER_TAB
        ]
        root: Any = records
        source_format = "jsonl"
    else:
        root = json.loads(text)
        records = root[:MAX_ROWS_PER_TAB] if isinstance(root, list) else [root]
        source_format = "json"
    facts = [
        _fact(
            "json_structure",
            "$",
            {
                "format": source_format,
                "shape": _json_shape(root),
                "record_count": len(root) if isinstance(root, list) else 1,
            },
        )
    ]
    documents: list[dict[str, Any]] = []
    for index, record in enumerate(records[:200], start=1):
        payload = _bounded_json_value(record)
        facts.append(_fact("json_record_sample", f"$[{index - 1}]", payload))
        serialized = json.dumps(
            record,
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        documents.append(
            {
                "locator": f"$[{index - 1}]",
                "text": serialized,
                "metadata": {"source_format": source_format},
            }
        )
    return documents, facts, encoding


def _extract_parquet(
    data: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    if not _module_available("pyarrow"):
        return (
            [],
            [
                _fact(
                    "parquet_schema",
                    "file",
                    {
                        "state": "BLOCKED_OPTIONAL_TOOL_UNAVAILABLE",
                        "required_module": "pyarrow",
                        "exact_bytes_preserved": True,
                    },
                )
            ],
            "BLOCKED_PARQUET_TOOL_UNAVAILABLE_EXACT_BYTES_PRESERVED",
        )
    from pyarrow import parquet  # type: ignore[import-not-found]

    parquet_file = parquet.ParquetFile(io.BytesIO(data))
    metadata = parquet_file.metadata
    facts = [
        _fact(
            "parquet_schema",
            "file",
            {
                "schema": str(parquet_file.schema_arrow),
                "columns": list(parquet_file.schema_arrow.names),
                "row_groups": parquet_file.num_row_groups,
                "rows": int(metadata.num_rows) if metadata is not None else None,
            },
        )
    ]
    documents: list[dict[str, Any]] = []
    sample_ordinal = 0
    for batch in parquet_file.iter_batches(batch_size=200):
        for record in batch.to_pylist():
            sample_ordinal += 1
            if sample_ordinal > 200:
                break
            payload = _bounded_json_value(record)
            facts.append(
                _fact(
                    "parquet_row_sample",
                    f"row:{sample_ordinal}",
                    payload,
                )
            )
            documents.append(
                {
                    "locator": f"row:{sample_ordinal}",
                    "text": json.dumps(
                        record,
                        sort_keys=True,
                        ensure_ascii=False,
                        default=str,
                    ),
                    "metadata": {},
                }
            )
        if sample_ordinal >= 200:
            break
    return documents, facts, "PARSED_PARQUET_PYARROW"


def _extract_legacy_excel(
    path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    if not _module_available("python_calamine"):
        return (
            [],
            [
                _fact(
                    "sheet_workbook",
                    "workbook",
                    {
                        "state": "BLOCKED_OPTIONAL_TOOL_UNAVAILABLE",
                        "required_module": "python_calamine",
                        "exact_bytes_preserved": True,
                    },
                )
            ],
            "BLOCKED_XLS_TOOL_UNAVAILABLE_EXACT_BYTES_PRESERVED",
        )
    from python_calamine import (  # type: ignore[import-not-found]
        CalamineWorkbook,
    )

    workbook = CalamineWorkbook.from_path(str(path))
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = [
        _fact(
            "sheet_workbook",
            "workbook",
            {
                "sheets": list(workbook.sheet_names),
                "extractor": "python_calamine",
            },
        )
    ]
    for sheet_name in workbook.sheet_names:
        rows = workbook.get_sheet_by_name(sheet_name).to_python(skip_empty_area=False)
        bounded = rows[:MAX_ROWS_PER_TAB]
        normalized = "\n".join(
            " | ".join("" if value is None else str(value) for value in row)
            for row in bounded
        )
        if normalized:
            documents.append(
                {
                    "locator": sheet_name,
                    "text": normalized,
                    "metadata": {"bounded_rows": len(bounded)},
                }
            )
        max_columns = max((len(row) for row in bounded), default=0)
        facts.append(
            _fact(
                "sheet_tab",
                sheet_name,
                {
                    "bounded_rows": len(bounded),
                    "columns_max": max_columns,
                    "row_limit": MAX_ROWS_PER_TAB,
                    "extractor": "python_calamine",
                },
            )
        )
        facts.append(
            _fact(
                "sheet_range",
                sheet_name,
                {
                    "range": (
                        f"A1:{_excel_column(max_columns)}{len(bounded)}"
                        if bounded and max_columns
                        else None
                    ),
                    "sampled_cells": sum(len(row) for row in bounded[:200]),
                },
            )
        )
        for row_index, row in enumerate(bounded[:200], start=1):
            for column_index, value in enumerate(row[:200], start=1):
                cell = f"{_excel_column(column_index)}{row_index}"
                facts.append(
                    _fact(
                        "sheet_cell_sample",
                        f"{sheet_name}!{cell}",
                        {
                            "sheet": sheet_name,
                            "cell": cell,
                            "value": value,
                            "extractor": "python_calamine",
                        },
                    )
                )
    return documents, facts, "PARSED_XLS_CALAMINE"


def _excel_column(index: int) -> str:
    if index <= 0:
        return ""
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _extract_pptx(data: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = sorted(
            name
            for name in archive.namelist()
            if re.fullmatch(r"ppt/(slides/slide|notesSlides/notesSlide)\d+\.xml", name)
        )
        for name in members:
            root = ElementTree.fromstring(archive.read(name))
            text = _xml_text(root)
            if text:
                documents.append({"locator": name, "text": text, "metadata": {}})
            is_slide = "/slides/" in name
            facts.append(
                _fact(
                    "ppt_slide" if is_slide else "ppt_notes",
                    name,
                    {
                        "text_nodes": sum(
                            1 for node in root.iter() if node.tag.endswith("}t")
                        ),
                        "shapes": sum(
                            1 for node in root.iter() if node.tag.endswith("}sp")
                        ),
                        "tables": sum(
                            1 for node in root.iter() if node.tag.endswith("}tbl")
                        ),
                    },
                )
            )
            if is_slide:
                for shape_index, shape in enumerate(
                    (node for node in root.iter() if node.tag.endswith("}sp")),
                    start=1,
                ):
                    non_visual = next(
                        (node for node in shape.iter() if node.tag.endswith("}cNvPr")),
                        None,
                    )
                    shape_id = (
                        non_visual.attrib.get("id", str(shape_index))
                        if non_visual is not None
                        else str(shape_index)
                    )
                    shape_name = (
                        non_visual.attrib.get("name", "")
                        if non_visual is not None
                        else ""
                    )
                    shape_text = _xml_text(shape).strip()
                    facts.append(
                        _fact(
                            "ppt_shape",
                            f"{name}:shape:{shape_id}",
                            {
                                "shape_id": shape_id,
                                "name": shape_name,
                                "text_chars": len(shape_text),
                            },
                        )
                    )
                    if shape_text:
                        facts.append(
                            _fact(
                                "ppt_text_block",
                                f"{name}:shape:{shape_id}:text",
                                {
                                    "shape_id": shape_id,
                                    "name": shape_name,
                                    "text": shape_text,
                                    "text_sha256": sha256_bytes(
                                        shape_text.encode("utf-8")
                                    ),
                                },
                            )
                        )
                for table_index, table in enumerate(
                    (node for node in root.iter() if node.tag.endswith("}tbl")),
                    start=1,
                ):
                    rows: list[list[str]] = []
                    for row in (node for node in table if node.tag.endswith("}tr")):
                        cells = [
                            _xml_text(cell).strip()
                            for cell in row
                            if cell.tag.endswith("}tc")
                        ]
                        if cells:
                            rows.append(cells)
                    facts.append(
                        _fact(
                            "ppt_table",
                            f"{name}:table:{table_index}",
                            {
                                "rows": rows[:200],
                                "row_count": len(rows),
                                "column_count_max": max(
                                    (len(row) for row in rows), default=0
                                ),
                                "truncated": len(rows) > 200,
                            },
                        )
                    )
                for picture_index, picture in enumerate(
                    (node for node in root.iter() if node.tag.endswith("}pic")),
                    start=1,
                ):
                    non_visual = next(
                        (
                            node
                            for node in picture.iter()
                            if node.tag.endswith("}cNvPr")
                        ),
                        None,
                    )
                    blip = next(
                        (node for node in picture.iter() if node.tag.endswith("}blip")),
                        None,
                    )
                    relationship_id = ""
                    if blip is not None:
                        relationship_id = next(
                            (
                                str(value)
                                for key, value in blip.attrib.items()
                                if key.endswith(("}embed", "}link"))
                            ),
                            "",
                        )
                    facts.append(
                        _fact(
                            "ppt_image_reference",
                            f"{name}:image:{picture_index}",
                            {
                                "relationship_id": relationship_id,
                                "name": (
                                    non_visual.attrib.get("name", "")
                                    if non_visual is not None
                                    else ""
                                ),
                                "description": (
                                    non_visual.attrib.get("descr", "")
                                    if non_visual is not None
                                    else ""
                                ),
                            },
                        )
                    )
            relationship_member = posixpath.join(
                posixpath.dirname(name),
                "_rels",
                f"{posixpath.basename(name)}.rels",
            )
            if relationship_member in archive.namelist():
                relationships = ElementTree.fromstring(
                    archive.read(relationship_member)
                )
                for relationship in relationships:
                    relationship_id = relationship.attrib.get("Id", "")
                    target = relationship.attrib.get("Target", "")
                    relationship_type = relationship.attrib.get("Type", "")
                    facts.append(
                        _fact(
                            "ppt_slide_relationship",
                            f"{name}:{relationship_id}",
                            {
                                "relationship_id": relationship_id,
                                "target": target,
                                "relationship_type": relationship_type,
                                "external": (
                                    relationship.attrib.get("TargetMode") == "External"
                                ),
                            },
                        )
                    )
                    if relationship_type.endswith("/image") and not any(
                        item["kind"] == "ppt_image_reference"
                        and item["payload"].get("relationship_id") == relationship_id
                        for item in facts
                    ):
                        facts.append(
                            _fact(
                                "ppt_image_reference",
                                f"{name}:relationship:{relationship_id}",
                                {
                                    "relationship_id": relationship_id,
                                    "target": target,
                                    "derived_from_relationship": True,
                                },
                            )
                        )
    return documents, facts


@lru_cache(maxsize=1)
def _rapidocr_engine() -> tuple[str, Any] | None:
    if _module_available("rapidocr"):
        from rapidocr import (
            RapidOCR as RapidOCREngine,  # type: ignore[import-not-found]
        )

        return "rapidocr+onnxruntime", RapidOCREngine()
    if _module_available("rapidocr_onnxruntime"):
        from rapidocr_onnxruntime import (  # type: ignore[import-not-found]
            RapidOCR as RapidOCROnnxEngine,
        )

        return "rapidocr_onnxruntime", RapidOCROnnxEngine()
    return None


def prewarm_native_dependencies() -> tuple[str, ...]:
    """Load optional native engines before an MCP/ASGI event loop starts.

    On Windows, first-time NumPy/OpenCV/ONNX loading from inside a running MCP
    request can block the host far longer than the same import at process
    startup.  The cached OCR engine is process-local, so this bounded startup
    step removes that lifecycle collision without serializing independent lane
    builders.
    """
    cached = _rapidocr_engine()
    return () if cached is None else (cached[0],)


def _json_safe(value: Any) -> Any:
    if hasattr(value, "tolist"):
        return value.tolist()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return str(value)


def _rapidocr_lines(image_data: bytes) -> tuple[list[dict[str, Any]], str | None]:
    # PDF and image lanes may build concurrently. The cached ONNX-backed OCR
    # object is process-local and is not documented as safe for simultaneous
    # calls, so only this shared external-engine boundary is serialized.
    with _RAPIDOCR_CALL_LOCK:
        cached = _rapidocr_engine()
        if cached is None:
            return [], "OCR_ENGINE_UNAVAILABLE"
        engine_name, engine = cached
        try:
            result = engine(image_data)
        except Exception as exc:  # noqa: BLE001 - external engine failure is evidence
            return [], f"OCR_ENGINE_ERROR_{type(exc).__name__.upper()}"
    lines: list[dict[str, Any]] = []
    if hasattr(result, "txts"):
        texts_value = getattr(result, "txts", None)
        boxes_value = getattr(result, "boxes", None)
        scores_value = getattr(result, "scores", None)
        texts = list(texts_value) if texts_value is not None else []
        boxes = list(boxes_value) if boxes_value is not None else []
        scores = list(scores_value) if scores_value is not None else []
        for index, text in enumerate(texts):
            lines.append(
                {
                    "text": str(text),
                    "confidence": (
                        float(scores[index]) if index < len(scores) else None
                    ),
                    "box": _json_safe(boxes[index]) if index < len(boxes) else None,
                    "engine": engine_name,
                }
            )
        return lines, None if lines else "RAPIDOCR_EMPTY"
    raw_result = result[0] if isinstance(result, tuple) else result
    for item in raw_result or []:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        lines.append(
            {
                "box": _json_safe(item[0]),
                "text": str(item[1]),
                "confidence": (
                    float(item[2]) if len(item) > 2 and item[2] is not None else None
                ),
                "engine": engine_name,
            }
        )
    return lines, None if lines else "RAPIDOCR_EMPTY"


def _ocr_payload(
    *,
    prefix: str,
    locator: str,
    lines: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    text = "\n".join(
        str(line["text"]).strip()
        for line in lines
        if str(line.get("text") or "").strip()
    )
    if not text:
        return [], []
    engine = str(lines[0]["engine"])
    documents = [
        {
            "locator": f"{locator}:ocr",
            "text": text,
            "metadata": {
                "ocr": True,
                "ocr_text_chars": len(text),
                "engine": engine,
            },
        }
    ]
    facts = [
        _fact(
            f"{prefix}_ocr_run",
            locator,
            {
                "engine": engine,
                "text_chars": len(text),
                "line_count": len(lines),
            },
        ),
        _fact(
            f"{prefix}_ocr_block",
            f"{locator}:ocr",
            {
                "text_chars": len(text),
                "text_sha256": sha256_bytes(text.encode("utf-8")),
            },
        ),
    ]
    facts.extend(
        _fact(
            f"{prefix}_ocr_line",
            f"{locator}:ocr:line:{index}",
            {
                "text": str(line["text"]),
                "text_sha256": sha256_bytes(str(line["text"]).encode("utf-8")),
                "confidence": line.get("confidence"),
                "box": line.get("box"),
                "engine": line.get("engine"),
            },
        )
        for index, line in enumerate(lines, start=1)
        if str(line.get("text") or "").strip()
    )
    return documents, facts


def _pytesseract_lines(image_data: bytes) -> tuple[list[dict[str, Any]], str | None]:
    if (
        not _module_available("PIL")
        or not _module_available("pytesseract")
        or not shutil.which("tesseract")
    ):
        return [], "PYTESSERACT_ROUTE_UNAVAILABLE"
    try:
        import pytesseract  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]

        image = Image.open(io.BytesIO(image_data))
        text = pytesseract.image_to_string(image)
    except Exception as exc:  # noqa: BLE001 - external engine failure is evidence
        return [], f"PYTESSERACT_ERROR_{type(exc).__name__.upper()}"
    return (
        [
            {
                "text": line,
                "confidence": None,
                "box": None,
                "engine": "pytesseract+tesseract",
            }
            for line in text.splitlines()
            if line.strip()
        ],
        None,
    )


def _extract_pdf(
    data: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    parser_errors: list[str] = []
    parser_state = "OPAQUE_EXACT_BYTES"
    page_count = 0
    renderer_available = False
    if _module_available("fitz"):
        try:
            import fitz  # type: ignore[import-not-found]

            document = fitz.open(stream=data, filetype="pdf")
            renderer_available = True
            try:
                page_count = min(len(document), MAX_PDF_PAGES)
                for page_index in range(page_count):
                    page = document[page_index]
                    text = page.get_text("text") or ""
                    locator = f"page:{page_index + 1}"
                    blocks = page.get_text("blocks")
                    images = page.get_images(full=True)
                    documents.append(
                        {
                            "locator": locator,
                            "text": text,
                            "metadata": {
                                "width": page.rect.width,
                                "height": page.rect.height,
                                "native_text_chars": len(text),
                            },
                        }
                    )
                    facts.append(
                        _fact(
                            "pdf_page",
                            locator,
                            {
                                "native_text_chars": len(text),
                                "text_blocks": len(blocks),
                                "images": len(images),
                                "width": page.rect.width,
                                "height": page.rect.height,
                            },
                        )
                    )
                    for block_index, block in enumerate(blocks, start=1):
                        block_text = str(block[4] or "") if len(block) > 4 else ""
                        facts.append(
                            _fact(
                                "pdf_text_block",
                                f"{locator}:native:{block_index}",
                                {
                                    "bbox": list(block[:4]),
                                    "text_chars": len(block_text),
                                    "text_sha256": sha256_bytes(
                                        block_text.encode("utf-8")
                                    ),
                                },
                            )
                        )
                    for image_index, image_info in enumerate(images, start=1):
                        facts.append(
                            _fact(
                                "pdf_image_block",
                                f"{locator}:image:{image_index}",
                                {
                                    "xref": image_info[0],
                                    "width": image_info[2],
                                    "height": image_info[3],
                                },
                            )
                        )
                parser_state = "PARSED_PYMUPDF"
            finally:
                document.close()
        except Exception as exc:  # noqa: BLE001 - parser fallback must remain open
            parser_errors.append(f"PYMUPDF_{type(exc).__name__.upper()}")
            documents.clear()
            facts.clear()
    if not documents and _module_available("pypdf"):
        try:
            from pypdf import PdfReader  # type: ignore[import-not-found]

            reader = PdfReader(io.BytesIO(data), strict=False)
            page_count = min(len(reader.pages), MAX_PDF_PAGES)
            for page_index, page in enumerate(reader.pages[:page_count], start=1):
                text = page.extract_text() or ""
                locator = f"page:{page_index}"
                documents.append(
                    {
                        "locator": locator,
                        "text": text,
                        "metadata": {"native_text_chars": len(text)},
                    }
                )
                facts.extend(
                    [
                        _fact(
                            "pdf_page",
                            locator,
                            {"native_text_chars": len(text)},
                        ),
                        _fact(
                            "pdf_text_block",
                            f"{locator}:native",
                            {
                                "text_chars": len(text),
                                "text_sha256": sha256_bytes(text.encode("utf-8")),
                            },
                        ),
                    ]
                )
            parser_state = "PARSED_PYPDF"
        except Exception as exc:  # noqa: BLE001 - parser fallback must remain open
            parser_errors.append(f"PYPDF_{type(exc).__name__.upper()}")
            documents.clear()
            facts.clear()
    if not documents and _module_available("pdfplumber"):
        try:
            import pdfplumber  # type: ignore[import-not-found]

            with pdfplumber.open(io.BytesIO(data)) as document:
                page_count = min(len(document.pages), MAX_PDF_PAGES)
                for page_index, page in enumerate(document.pages[:page_count], start=1):
                    text = page.extract_text() or ""
                    locator = f"page:{page_index}"
                    documents.append(
                        {
                            "locator": locator,
                            "text": text,
                            "metadata": {
                                "native_text_chars": len(text),
                                "width": page.width,
                                "height": page.height,
                            },
                        }
                    )
                    facts.extend(
                        [
                            _fact(
                                "pdf_page",
                                locator,
                                {
                                    "native_text_chars": len(text),
                                    "width": page.width,
                                    "height": page.height,
                                },
                            ),
                            _fact(
                                "pdf_text_block",
                                f"{locator}:native",
                                {
                                    "text_chars": len(text),
                                    "text_sha256": sha256_bytes(text.encode("utf-8")),
                                },
                            ),
                        ]
                    )
            parser_state = "PARSED_PDFPLUMBER"
        except Exception as exc:  # noqa: BLE001 - parser fallback must remain open
            parser_errors.append(f"PDFPLUMBER_{type(exc).__name__.upper()}")
            documents.clear()
            facts.clear()
    native_chars = sum(len(str(item.get("text") or "")) for item in documents)
    text_poor = native_chars < max(10, max(page_count, 1) * 5)
    if text_poor and renderer_available and _module_available("fitz"):
        import fitz  # type: ignore[import-not-found]

        try:
            document = fitz.open(stream=data, filetype="pdf")
            try:
                ocr_documents: list[dict[str, Any]] = []
                ocr_facts: list[dict[str, Any]] = []
                for page_index in range(min(len(document), MAX_PDF_PAGES)):
                    locator = f"page:{page_index + 1}"
                    pixmap = document[page_index].get_pixmap(
                        matrix=fitz.Matrix(2, 2),
                        alpha=False,
                    )
                    image_data = pixmap.tobytes("png")
                    lines, blocker = _rapidocr_lines(image_data)
                    if not lines:
                        secondary_lines, secondary = _pytesseract_lines(image_data)
                        lines = secondary_lines
                        blocker = " | ".join(
                            item for item in (blocker, secondary) if item
                        )
                    page_documents, page_facts = _ocr_payload(
                        prefix="pdf",
                        locator=locator,
                        lines=lines,
                    )
                    ocr_documents.extend(page_documents)
                    ocr_facts.extend(page_facts)
                    if not page_documents:
                        if blocker and "RAPIDOCR_EMPTY" in blocker:
                            ocr_facts.append(
                                _fact(
                                    "pdf_ocr_run",
                                    locator,
                                    {
                                        "engine": "rapidocr+onnxruntime",
                                        "state": "EMPTY",
                                        "text_chars": 0,
                                        "line_count": 0,
                                    },
                                )
                            )
                        ocr_facts.append(
                            _fact(
                                "pdf_review_region",
                                locator,
                                {
                                    "reason": blocker or "OCR_EMPTY",
                                    "native_text_chars": sum(
                                        len(str(item.get("text") or ""))
                                        for item in documents
                                        if item.get("locator") == locator
                                    ),
                                    "exact_bytes_preserved": True,
                                },
                            )
                        )
                if ocr_documents:
                    documents = ocr_documents
                    facts.extend(ocr_facts)
                    parser_state = "PARSED_OCR_LOCAL"
                else:
                    facts.extend(ocr_facts)
            finally:
                document.close()
        except Exception as exc:  # noqa: BLE001 - OCR failure is governed evidence
            parser_errors.append(f"PDF_OCR_{type(exc).__name__.upper()}")
    elif text_poor:
        facts.append(
            _fact(
                "pdf_review_region",
                "file",
                {
                    "reason": (
                        "PDF_RASTERIZER_UNAVAILABLE"
                        if not renderer_available
                        else "OCR_ENGINE_UNAVAILABLE"
                    ),
                    "parser_errors": parser_errors,
                    "native_text_chars": native_chars,
                    "exact_bytes_preserved": True,
                },
            )
        )
    if not documents and parser_state == "OPAQUE_EXACT_BYTES":
        parser_state = (
            "BLOCKED_PDF_CAPABILITY_UNAVAILABLE_EXACT_BYTES_PRESERVED"
            if not parser_errors
            else "PARSE_FAILED_PDF_EXACT_BYTES_PRESERVED"
        )
    return documents, facts, parser_state


def _extract_image(
    data: bytes,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    parser_state = "OPAQUE_EXACT_BYTES"
    metadata_error: str | None = None
    if _module_available("PIL"):
        try:
            from PIL import Image  # type: ignore[import-not-found]

            image = Image.open(io.BytesIO(data))
            facts.append(
                _fact(
                    "image_metadata",
                    "image",
                    {
                        "format": image.format,
                        "width": image.width,
                        "height": image.height,
                        "mode": image.mode,
                        "frames": getattr(image, "n_frames", 1),
                    },
                )
            )
            parser_state = "METADATA_PARSED"
        except Exception as exc:  # noqa: BLE001 - image decoder is optional
            metadata_error = f"IMAGE_METADATA_{type(exc).__name__.upper()}"
    lines, blocker = _rapidocr_lines(data)
    if not lines:
        secondary_lines, secondary = _pytesseract_lines(data)
        lines = secondary_lines
        blocker = " | ".join(item for item in (blocker, secondary) if item)
    ocr_documents, ocr_facts = _ocr_payload(
        prefix="image",
        locator="image",
        lines=lines,
    )
    documents.extend(ocr_documents)
    facts.extend(ocr_facts)
    if ocr_documents:
        parser_state = "PARSED_OCR_LOCAL"
    else:
        if blocker and "RAPIDOCR_EMPTY" in blocker:
            facts.append(
                _fact(
                    "image_ocr_run",
                    "image",
                    {
                        "engine": "rapidocr+onnxruntime",
                        "state": "EMPTY",
                        "text_chars": 0,
                        "line_count": 0,
                    },
                )
            )
        facts.append(
            _fact(
                "image_review_region",
                "image",
                {
                    "reason": blocker or metadata_error or "OCR_EMPTY",
                    "metadata_error": metadata_error,
                    "exact_bytes_preserved": True,
                },
            )
        )
        if parser_state == "OPAQUE_EXACT_BYTES":
            parser_state = (
                "PARSE_FAILED_IMAGE_EXACT_BYTES_PRESERVED"
                if metadata_error
                else "BLOCKED_IMAGE_CAPABILITY_UNAVAILABLE_EXACT_BYTES_PRESERVED"
            )
    return documents, facts, parser_state


def _inspect_sqlite(
    path: Path,
    lane: LaneDefinition,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    connection: sqlite3.Connection | None = None
    is_loader = lane.canonical_lane_id == "brain_loader"
    database_kind = (
        "brain_loader_database" if is_loader else "loaded_sqlite_brain_database"
    )
    schema_kind = (
        "brain_loader_schema_object"
        if is_loader
        else "loaded_sqlite_brain_schema_object"
    )
    relationship_kind = (
        "brain_loader_relationship" if is_loader else "loaded_sqlite_brain_relationship"
    )
    receipt_kind = (
        "brain_loader_receipt" if is_loader else "loaded_sqlite_brain_receipt"
    )
    try:
        connection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
            timeout=10,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        foreign_key_errors = [
            dict(row)
            for row in connection.execute("PRAGMA foreign_key_check").fetchmany(1001)
        ]
        schema_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT type, name, tbl_name, sql
                FROM sqlite_master
                WHERE name NOT LIKE 'sqlite_%'
                ORDER BY type, name
                """
            )
        ]
        foreign_keys: list[dict[str, Any]] = []
        table_stats: list[dict[str, Any]] = []
        fts_tables: list[dict[str, Any]] = []
        for row in schema_rows:
            if row["type"] == "table":
                safe_name = str(row["name"]).replace('"', '""')
                table_fks = [
                    {
                        **dict(fk),
                        "from_table": row["name"],
                        "to_table": dict(fk).get("table"),
                    }
                    for fk in connection.execute(
                        f'PRAGMA foreign_key_list("{safe_name}")'  # nosec B608
                    )
                ]
                foreign_keys.extend(table_fks)
                sql = str(row.get("sql") or "")
                is_virtual = sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE")
                is_fts = is_virtual and "USING FTS" in sql.upper()
                if is_fts:
                    fts_tables.append(
                        {
                            "table": row["name"],
                            "sql_sha256": sha256_bytes(sql.encode("utf-8")),
                        }
                    )
                row_count: int | None = None
                count_error: str | None = None
                if not is_virtual:
                    try:
                        row_count = int(
                            connection.execute(
                                f'SELECT COUNT(*) FROM "{safe_name}"'  # nosec B608
                            ).fetchone()[0]
                        )
                    except sqlite3.Error as exc:
                        count_error = type(exc).__name__
                table_stats.append(
                    {
                        "table": row["name"],
                        "row_count": row_count,
                        "count_error_type": count_error,
                        "virtual": is_virtual,
                        "fts": is_fts,
                    }
                )
        text = "\n\n".join(
            f"{row['type']} {row['name']}\n{row.get('sql') or ''}"
            for row in schema_rows
        )
        documents.append({"locator": "sqlite_schema", "text": text, "metadata": {}})
        facts.append(
            _fact(
                database_kind,
                path.name,
                {
                    "size_bytes": path.stat().st_size,
                    "schema_objects": len(schema_rows),
                    "tables": sum(row["type"] == "table" for row in schema_rows),
                    "views": sum(row["type"] == "view" for row in schema_rows),
                    "indexes": sum(row["type"] == "index" for row in schema_rows),
                    "triggers": sum(row["type"] == "trigger" for row in schema_rows),
                    "inspection_mode": "sqlite_uri_ro_immutable_query_only",
                },
            )
        )
        facts.extend(_fact(schema_kind, str(row["name"]), row) for row in schema_rows)
        for foreign_key in foreign_keys:
            facts.append(
                _fact(
                    (
                        relationship_kind
                        if is_loader
                        else "loaded_sqlite_brain_foreign_key"
                    ),
                    (
                        f"{foreign_key['from_table']}:{foreign_key.get('id')}:"
                        f"{foreign_key.get('seq')}"
                    ),
                    foreign_key,
                )
            )
            if not is_loader:
                facts.append(
                    _fact(
                        relationship_kind,
                        (
                            f"{foreign_key['from_table']}."
                            f"{foreign_key.get('from')}->"
                            f"{foreign_key.get('to_table')}."
                            f"{foreign_key.get('to')}"
                        ),
                        {
                            "from_table": foreign_key["from_table"],
                            "from_column": foreign_key.get("from"),
                            "to_table": foreign_key.get("to_table"),
                            "to_column": foreign_key.get("to"),
                        },
                    )
                )
        if not is_loader:
            facts.extend(
                _fact(
                    "loaded_sqlite_brain_table_stat",
                    str(stat["table"]),
                    stat,
                )
                for stat in table_stats
            )
            facts.extend(
                _fact(
                    "loaded_sqlite_brain_fts_table",
                    str(item["table"]),
                    item,
                )
                for item in fts_tables
            )
            facts.append(
                _fact(
                    "loaded_sqlite_brain_integrity_result",
                    "PRAGMA integrity_check",
                    {
                        "rows": integrity,
                        "valid": integrity == ["ok"] and not foreign_key_errors,
                        "foreign_key_error_count": len(foreign_key_errors),
                        "foreign_key_errors": foreign_key_errors[:1000],
                        "foreign_key_errors_truncated": len(foreign_key_errors) > 1000,
                    },
                )
            )
            facts.append(
                _fact(
                    "loaded_sqlite_brain_compatibility",
                    "schema",
                    {
                        "read_only_open": True,
                        "integrity_ok": integrity == ["ok"],
                        "foreign_keys_ok": not foreign_key_errors,
                        "fts_table_count": len(fts_tables),
                        "imported_sql_executed": False,
                    },
                )
            )
        facts.append(
            _fact(
                receipt_kind,
                "read_only_inspection",
                {
                    "status": (
                        "PASS"
                        if integrity == ["ok"] and not foreign_key_errors
                        else "FAIL"
                    ),
                    "rows": integrity,
                    "schema_objects": len(schema_rows),
                    "table_stats": len(table_stats),
                    "foreign_keys": len(foreign_keys),
                    "fts_tables": len(fts_tables),
                    "foreign_key_errors": len(foreign_key_errors),
                    "writeback": False,
                    "imported_sql_executed": False,
                },
            )
        )
        return documents, facts, "PARSED_SQLITE_READ_ONLY"
    except sqlite3.Error as exc:
        failure_kind = (
            "brain_loader_receipt"
            if is_loader
            else "loaded_sqlite_brain_integrity_result"
        )
        return (
            [],
            [
                _fact(
                    failure_kind,
                    "open",
                    {
                        "valid": False,
                        "error_type": type(exc).__name__,
                        "writeback": False,
                        "imported_sql_executed": False,
                    },
                )
            ],
            "PARSE_FAILED_SQLITE_EXACT_BYTES_PRESERVED",
        )
    finally:
        if connection is not None:
            connection.close()


def _archive_member_safe(name: str) -> bool:
    raw = name.replace("\\", "/")
    normalized = posixpath.normpath(raw)
    segments = [segment for segment in raw.split("/") if segment not in {"", "."}]
    return bool(
        normalized
        and normalized not in {".", ".."}
        and not normalized.startswith("../")
        and not normalized.startswith("/")
        and not re.match(r"^[A-Za-z]:", normalized)
        and ".." not in segments
    )


def _extract_archive(
    data: bytes,
    lane: LaneDefinition,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [
            {
                "name": info.filename.replace("\\", "/"),
                "bytes": info.file_size,
                "compressed_bytes": info.compress_size,
                "is_directory": info.is_dir(),
                "safe_path": _archive_member_safe(info.filename),
                "crc32": f"{info.CRC:08X}",
            }
            for info in archive.infolist()[:10000]
        ]
    text = "\n".join(
        f"{item['name']} ({item['bytes']} bytes)"
        for item in members
        if not item["is_directory"]
    )
    if text:
        documents.append(
            {
                "locator": "archive_members",
                "text": text,
                "metadata": {"member_count": len(members)},
            }
        )
    unsafe = [item["name"] for item in members if not item["safe_path"]]
    lane_id = lane.canonical_lane_id
    if lane_id == "brain_loader":
        facts.append(
            _fact(
                "brain_loader_package",
                "archive",
                {
                    "member_count": len(members),
                    "unsafe_member_count": len(unsafe),
                    "exact_bytes_preserved": True,
                    "members_executed": False,
                },
            )
        )
        facts.extend(
            _fact("brain_loader_member", str(item["name"]), item) for item in members
        )
        for item in members:
            lowered = str(item["name"]).lower()
            if "manifest" in lowered and lowered.endswith(".json"):
                facts.append(_fact("brain_loader_manifest", str(item["name"]), item))
            if "pointer" in lowered and lowered.endswith((".json", ".txt")):
                facts.append(_fact("brain_loader_pointer", str(item["name"]), item))
        facts.append(
            _fact(
                "brain_loader_receipt",
                "archive_inspection",
                {
                    "status": "PASS" if not unsafe else "BLOCKED",
                    "unsafe_members": unsafe,
                    "members_executed": False,
                    "writeback": False,
                },
            )
        )
        state = (
            "PARSED_BRAIN_PACKAGE_DIRECTORY"
            if not unsafe
            else "BLOCKED_BRAIN_PACKAGE_UNSAFE_MEMBER"
        )
    elif lane_id == "project_engulf":
        facts.append(
            _fact(
                "project_engulf_origin",
                "archive",
                {
                    "origin_type": "zip_archive",
                    "member_count": len(members),
                    "exact_bytes_preserved": True,
                },
            )
        )
        components: dict[str, int] = {}
        for item in members:
            name = str(item["name"])
            facts.append(_fact("project_engulf_file", name, item))
            top_level = name.split("/", 1)[0] if name else ""
            if top_level:
                components[top_level] = components.get(top_level, 0) + 1
            if not item["is_directory"] and item["safe_path"]:
                target_lane = route_source(name, code_mode="local_code")
                facts.extend(
                    [
                        _fact(
                            "project_engulf_sector_target",
                            name,
                            {
                                "target_lane": target_lane,
                                "classification_only": True,
                                "writeback": False,
                            },
                        ),
                        _fact(
                            "project_engulf_schema_mapping",
                            name,
                            {
                                "extension": Path(name).suffix.lower(),
                                "target_lane": target_lane,
                            },
                        ),
                        _fact(
                            "project_engulf_object_decision",
                            name,
                            {
                                "decision": "INDEX_ONLY_PENDING_HIL",
                                "target_lane": target_lane,
                            },
                        ),
                        _fact(
                            "project_engulf_relationship",
                            f"archive->{name}",
                            {
                                "from": "archive",
                                "to": name,
                                "relation": "contains",
                            },
                        ),
                    ]
                )
            elif not item["safe_path"]:
                facts.append(
                    _fact(
                        "project_engulf_conflict",
                        name,
                        {"reason": "UNSAFE_ARCHIVE_MEMBER_PATH"},
                    )
                )
        facts.extend(
            _fact(
                "project_engulf_component",
                component,
                {"member_count": member_count},
            )
            for component, member_count in sorted(components.items())
        )
        facts.extend(
            [
                _fact(
                    "project_engulf_run",
                    "archive_inspection",
                    {
                        "status": "PASS" if not unsafe else "BLOCKED",
                        "member_count": len(members),
                        "writeback": False,
                    },
                ),
                _fact(
                    "project_engulf_topology_update",
                    "proposed",
                    {
                        "state": "PROPOSED_NOT_APPLIED",
                        "component_count": len(components),
                    },
                ),
                _fact(
                    "project_engulf_receipt",
                    "archive_inspection",
                    {
                        "status": "PASS" if not unsafe else "BLOCKED",
                        "unsafe_members": unsafe,
                        "mutated_project": False,
                    },
                ),
            ]
        )
        state = (
            "PARSED_PROJECT_ARCHIVE_DIRECTORY"
            if not unsafe
            else "BLOCKED_PROJECT_ARCHIVE_UNSAFE_MEMBER"
        )
    elif lane_id == "sqlite_brain":
        database_members = [
            item
            for item in members
            if Path(str(item["name"])).suffix.lower() in {".db", ".sqlite", ".sqlite3"}
        ]
        facts.append(
            _fact(
                "loaded_sqlite_brain_package_pointer",
                "archive",
                {
                    "database_members": database_members,
                    "member_count": len(members),
                    "state": "INDEXED_NOT_LOADED",
                    "writeback": False,
                },
            )
        )
        facts.append(
            _fact(
                "loaded_sqlite_brain_receipt",
                "archive_inspection",
                {
                    "status": "PASS" if database_members and not unsafe else "BLOCKED",
                    "unsafe_members": unsafe,
                    "database_member_count": len(database_members),
                    "database_members_executed": False,
                },
            )
        )
        state = (
            "PARSED_SQLITE_BRAIN_PACKAGE_DIRECTORY"
            if database_members and not unsafe
            else "BLOCKED_SQLITE_BRAIN_PACKAGE"
        )
    else:
        facts.extend(
            _fact(
                "custom_item",
                str(item["name"]),
                item,
            )
            for item in members
        )
        state = "PARSED_ZIP_DIRECTORY" if not unsafe else "BLOCKED_ZIP_UNSAFE_MEMBER"
    return documents, facts, state


def _message_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_message_text(item) for item in value if item is not None)
    if isinstance(value, dict):
        if "parts" in value:
            return _message_text(value["parts"])
        if "text" in value:
            return _message_text(value["text"])
        if "content" in value:
            return _message_text(value["content"])
    return ""


def _chat_turns(value: Any) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    records = value if isinstance(value, list) else [value]
    for record in records:
        if not isinstance(record, dict):
            continue
        prompt = _message_text(
            record.get("prompt")
            or record.get("user")
            or record.get("input")
            or record.get("question")
        )
        response = _message_text(
            record.get("response")
            or record.get("assistant")
            or record.get("output")
            or record.get("answer")
        )
        if prompt or response:
            turns.append((prompt, response))
        messages = record.get("messages")
        if not isinstance(messages, list):
            continue
        pending_prompt = ""
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "").lower()
            text = _message_text(message.get("content"))
            if role in {"user", "human"}:
                if pending_prompt:
                    turns.append((pending_prompt, ""))
                pending_prompt = text
            elif role in {"assistant", "ai"}:
                turns.append((pending_prompt, text))
                pending_prompt = ""
        if pending_prompt:
            turns.append((pending_prompt, ""))
    if isinstance(value, dict) and isinstance(value.get("mapping"), dict):
        pending_prompt = ""
        ordered = sorted(
            value["mapping"].values(),
            key=lambda item: (
                (
                    item.get("message", {}).get("create_time")
                    if isinstance(item, dict)
                    else None
                )
                or 0,
                str(item.get("id", "")) if isinstance(item, dict) else "",
            ),
        )
        for node in ordered:
            if not isinstance(node, dict):
                continue
            message = node.get("message")
            if not isinstance(message, dict):
                continue
            author = message.get("author")
            role = (
                str(author.get("role") or "").lower()
                if isinstance(author, dict)
                else ""
            )
            text = _message_text(message.get("content"))
            if role in {"user", "human"}:
                if pending_prompt:
                    turns.append((pending_prompt, ""))
                pending_prompt = text
            elif role in {"assistant", "ai"}:
                turns.append((pending_prompt, text))
                pending_prompt = ""
        if pending_prompt:
            turns.append((pending_prompt, ""))
    return [(prompt, response) for prompt, response in turns if prompt or response]


def _chat_turns_from_text(text: str) -> list[tuple[str, str]]:
    pattern = re.compile(
        r"(?ims)^\s*(?:user|human)\s*:\s*(.+?)"
        r"^\s*(?:assistant|ai)\s*:\s*(.+?)"
        r"(?=^\s*(?:user|human)\s*:|\Z)"
    )
    return [
        (match.group(1).strip(), match.group(2).strip())
        for match in pattern.finditer(text)
    ]


def _chat_lineage_facts(
    turns: list[tuple[str, str]],
    relative_path: str,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    prior_hash = "0" * 64
    for ordinal, (prompt, response) in enumerate(turns[:5000], start=1):
        locator = f"{relative_path}:turn:{ordinal}"
        prepare = {
            "turn_ordinal": ordinal,
            "prompt_sha256": sha256_bytes(prompt.encode("utf-8")),
            "response_sha256": sha256_bytes(response.encode("utf-8")),
        }
        facts.extend(
            [
                _fact("turn_prepare", locator, prepare),
                _fact(
                    "prompt_raw_exact",
                    f"{locator}:prompt",
                    {"text": prompt, "sha256": prepare["prompt_sha256"]},
                ),
                _fact(
                    "prompt_normalized_summary",
                    f"{locator}:prompt-summary",
                    {
                        "visible_prefix": prompt[:500],
                        "truncated": len(prompt) > 500,
                    },
                ),
                _fact(
                    "response_raw_visible_exact",
                    f"{locator}:response",
                    {"text": response, "sha256": prepare["response_sha256"]},
                ),
                _fact(
                    "response_summary",
                    f"{locator}:response-summary",
                    {
                        "visible_prefix": response[:500],
                        "truncated": len(response) > 500,
                    },
                ),
            ]
        )
        commit_payload = {
            "turn_ordinal": ordinal,
            "prior_hash": prior_hash,
            "prompt_sha256": prepare["prompt_sha256"],
            "response_sha256": prepare["response_sha256"],
        }
        commit_hash = sha256_bytes(canonical_json_bytes(commit_payload))
        commit_payload["commit_hash"] = commit_hash
        facts.extend(
            [
                _fact("turn_commit", locator, commit_payload),
                _fact(
                    "state_hash_chain",
                    locator,
                    {
                        "prior_hash": prior_hash,
                        "current_hash": commit_hash,
                    },
                ),
            ]
        )
        prior_hash = commit_hash
    facts.extend(
        [
            _fact(
                "lineage_head",
                relative_path,
                {
                    "turn_count": min(len(turns), 5000),
                    "head_hash": prior_hash,
                    "truncated": len(turns) > 5000,
                },
            ),
            _fact(
                "source_normalization_receipt",
                relative_path,
                {
                    "turns_detected": len(turns),
                    "turns_indexed": min(len(turns), 5000),
                    "raw_source_preserved": True,
                    "private_reasoning_inferred": False,
                },
            ),
        ]
    )
    return facts


def _extract_source(
    path: Path,
    relative_path: str,
    data: bytes,
    lane: LaneDefinition,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str | None]:
    suffix = path.suffix.lower()
    parser_state = "OPAQUE_EXACT_BYTES"
    encoding: str | None = None

    def finish(
        documents: list[dict[str, Any]],
        facts: list[dict[str, Any]],
        state: str,
        detected_encoding: str | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str, str | None]:
        return _finalize_extraction(
            lane,
            relative_path,
            documents,
            facts,
            state,
            detected_encoding,
        )

    try:
        if suffix == ".docx":
            documents, facts = _extract_docx(data)
            return finish(documents, facts, "PARSED_DOCX_OPENXML", None)
        if suffix in {".xlsx", ".xlsm"}:
            documents, facts = _extract_xlsx(data)
            return finish(documents, facts, "PARSED_XLSX_OPENXML", None)
        if suffix == ".xls":
            documents, facts, state = _extract_legacy_excel(path)
            return finish(documents, facts, state, None)
        if suffix in {".csv", ".tsv"}:
            documents, facts, encoding = _extract_delimited(data, suffix)
            return finish(documents, facts, "PARSED_DELIMITED", encoding)
        if suffix in {".json", ".jsonl"}:
            documents, facts, encoding = _extract_json(data, suffix)
            decoded, _ = _decode_text(data)
            if decoded is not None and lane.canonical_lane_id in PRIMARY_CODE_LANES:
                facts.extend(extract_code_lane_facts(relative_path, decoded))
            if lane.canonical_lane_id == "chat_lineage":
                root: Any = []
                if decoded is not None:
                    if suffix == ".jsonl":
                        root = [
                            json.loads(line)
                            for line in decoded.splitlines()
                            if line.strip()
                        ]
                    else:
                        root = json.loads(decoded)
                facts.extend(_chat_lineage_facts(_chat_turns(root), relative_path))
            return finish(documents, facts, "PARSED_JSON", encoding)
        if suffix == ".parquet":
            documents, facts, state = _extract_parquet(data)
            return finish(documents, facts, state, None)
        if suffix == ".pptx":
            documents, facts = _extract_pptx(data)
            return finish(documents, facts, "PARSED_PPTX_OPENXML", None)
        if suffix == ".pdf":
            documents, facts, state = _extract_pdf(data)
            return finish(documents, facts, state, None)
        if suffix in LANE_REGISTRY["images_ocr"].extensions:
            documents, facts, state = _extract_image(data)
            return finish(documents, facts, state, None)
        if suffix in {".db", ".sqlite", ".sqlite3"}:
            documents, facts, state = _inspect_sqlite(path, lane)
            return finish(documents, facts, state, None)
        if suffix == ".zip":
            documents, facts, state = _extract_archive(data, lane)
            return finish(documents, facts, state, None)
        decoded_text, encoding = _decode_text(data)
        if decoded_text is not None:
            text_facts: list[dict[str, Any]] = []
            if lane.canonical_lane_id in PRIMARY_CODE_LANES:
                text_facts.extend(extract_code_lane_facts(relative_path, decoded_text))
            if lane.canonical_lane_id == "chat_lineage":
                text_facts.extend(
                    _chat_lineage_facts(
                        _chat_turns_from_text(decoded_text),
                        relative_path,
                    )
                )
            if suffix in {".html", ".htm", ".xml"}:
                stripped = re.sub(r"<[^>]+>", " ", decoded_text)
                decoded_text = re.sub(r"\s+", " ", stripped)
                text_facts.append(
                    _fact(
                        "document_structure",
                        relative_path,
                        {"source_format": suffix, "markup_stripped": True},
                    )
                )
            return finish(
                [
                    {
                        "locator": relative_path,
                        "text": decoded_text,
                        "metadata": {},
                    }
                ],
                text_facts,
                "PARSED_TEXT",
                encoding,
            )
        facts = [
            _fact(
                "parser_capability_blocker",
                relative_path,
                {
                    "reason": "NO_REGISTERED_PARSER_FOR_BINARY_FORMAT",
                    "extension": suffix,
                    "exact_bytes_preserved": True,
                },
            )
        ]
        return finish([], facts, parser_state, encoding)
    except Exception as exc:  # noqa: BLE001 - every parser failure becomes a fact
        return finish(
            [],
            [
                _fact(
                    "parser_error",
                    relative_path,
                    {
                        "error_type": type(exc).__name__,
                        "exact_bytes_preserved": True,
                    },
                )
            ],
            f"PARSE_FAILED_{type(exc).__name__.upper()}",
            encoding,
        )


def _chunks(text: str) -> Iterable[tuple[int, int, str]]:
    if not text:
        return
    start = 0
    ordinal = 0
    step = CHUNK_CHARS - CHUNK_OVERLAP
    while start < len(text):
        end = min(len(text), start + CHUNK_CHARS)
        yield ordinal, start, text[start:end]
        if end == len(text):
            break
        start += step
        ordinal += 1


def _create_lane_schema(connection: sqlite3.Connection, lane: LaneDefinition) -> None:
    fts = lane.fts_table
    if not re.fullmatch(r"[a-z][a-z0-9_]*", fts):
        raise ValueError(f"Unsafe FTS table name: {fts}")
    connection.executescript(
        f"""
        PRAGMA foreign_keys = ON;
        CREATE TABLE lane_meta(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        ) STRICT;
        CREATE TABLE lane_pointer(
            pointer_kind TEXT PRIMARY KEY,
            pointer_value TEXT,
            generation INTEGER NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE source_registry(
            source_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            size_bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            mime_type TEXT NOT NULL,
            extension TEXT NOT NULL,
            encoding TEXT,
            parser_state TEXT NOT NULL,
            exact_bytes BLOB NOT NULL,
            registered_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE source_tombstone(
            tombstone_id INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            prior_sha256 TEXT NOT NULL,
            prior_size_bytes INTEGER NOT NULL,
            removed_at TEXT NOT NULL,
            parent_pv TEXT
        ) STRICT;
        CREATE TABLE chunk_index(
            chunk_id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES source_registry(source_id) ON DELETE CASCADE,
            locator TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            char_start INTEGER NOT NULL,
            char_end INTEGER NOT NULL,
            text_content TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            UNIQUE(source_id, locator, ordinal)
        ) STRICT;
        CREATE TABLE chunk_content_cas(
            sha256 TEXT PRIMARY KEY,
            size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
            text_content TEXT NOT NULL,
            first_seen_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE chunk_history(
            history_id INTEGER PRIMARY KEY,
            source_path TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            locator TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            chunk_sha256 TEXT NOT NULL REFERENCES chunk_content_cas(sha256),
            snapshot_ref TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            content_reused INTEGER NOT NULL CHECK(content_reused IN (0, 1)),
            UNIQUE(snapshot_ref, source_path, locator, ordinal, chunk_sha256)
        ) STRICT;
        CREATE VIRTUAL TABLE {fts} USING fts5(
            path,
            locator,
            text_content,
            chunk_id UNINDEXED,
            tokenize='unicode61'
        );
        CREATE TABLE structured_fact(
            fact_id INTEGER PRIMARY KEY,
            source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            locator TEXT NOT NULL,
            payload_json TEXT NOT NULL
        ) STRICT;
        CREATE TABLE parser_capability(
            capability TEXT PRIMARY KEY,
            state TEXT NOT NULL,
            tool TEXT NOT NULL,
            detail TEXT NOT NULL
        ) STRICT;
        CREATE TABLE tfidf_term(
            term TEXT PRIMARY KEY,
            document_frequency INTEGER NOT NULL,
            document_count INTEGER NOT NULL,
            idf REAL NOT NULL
        ) STRICT;
        CREATE TABLE tfidf_vector(
            chunk_id INTEGER NOT NULL REFERENCES chunk_index(chunk_id) ON DELETE CASCADE,
            term TEXT NOT NULL REFERENCES tfidf_term(term) ON DELETE CASCADE,
            term_count INTEGER NOT NULL,
            token_count INTEGER NOT NULL,
            tf REAL NOT NULL,
            tfidf REAL NOT NULL,
            PRIMARY KEY(chunk_id, term)
        ) STRICT;
        CREATE TABLE refresh_receipt(
            receipt_id INTEGER PRIMARY KEY,
            build_mode TEXT NOT NULL,
            parent_pv TEXT,
            proposed_pv TEXT NOT NULL,
            unchanged_reuse INTEGER NOT NULL,
            changed_rebuild INTEGER NOT NULL,
            new_register INTEGER NOT NULL,
            removed_tombstone INTEGER NOT NULL,
            blocked_unsupported INTEGER NOT NULL,
            details_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        ) STRICT;
        CREATE TABLE mutation_receipt(
            mutation_id INTEGER PRIMARY KEY,
            mutation_kind TEXT NOT NULL,
            source_path TEXT,
            prior_sha256 TEXT,
            current_sha256 TEXT,
            recorded_at TEXT NOT NULL
        ) STRICT;
        CREATE INDEX source_registry_path_idx ON source_registry(path);
        CREATE INDEX chunk_source_idx ON chunk_index(source_id, ordinal);
        CREATE INDEX chunk_history_source_idx
        ON chunk_history(source_path, snapshot_ref, ordinal);
        CREATE INDEX structured_fact_kind_idx ON structured_fact(kind);
        """
    )
    if lane.canonical_lane_id in PRIMARY_CODE_LANES:
        create_git_history_schema(connection)
    shared_tables = {
        "lane_meta",
        "lane_pointer",
        "source_registry",
        "source_tombstone",
        "chunk_index",
        "chunk_content_cas",
        "chunk_history",
        "structured_fact",
        "parser_capability",
        "tfidf_term",
        "tfidf_vector",
        "refresh_receipt",
        "mutation_receipt",
        lane.fts_table,
    }
    for table in lane.schema_contract:
        if table in shared_tables:
            continue
        if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
            raise ValueError(f"Unsafe lane schema table name: {table}")
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table}(
                record_id INTEGER PRIMARY KEY,
                source_id INTEGER REFERENCES source_registry(source_id) ON DELETE CASCADE,
                locator TEXT NOT NULL,
                payload_json TEXT NOT NULL
            ) STRICT
            """
        )


def _open_lane(
    path: Path, lane: LaneDefinition, *, initialize: bool
) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    if initialize:
        _create_lane_schema(connection, lane)
    return connection


def _insert_source(
    connection: sqlite3.Connection,
    lane: LaneDefinition,
    root: Path,
    relative_path: str,
    *,
    registered_at: str,
    snapshot_ref: str,
) -> tuple[int, str]:
    path = root / Path(relative_path)
    data = path.read_bytes()
    if len(data) > MAX_EXTRACT_BYTES:
        documents: list[dict[str, Any]] = []
        facts = [
            {
                "kind": "parser_limit",
                "locator": relative_path,
                "payload": {
                    "size_bytes": len(data),
                    "max_extract_bytes": MAX_EXTRACT_BYTES,
                },
            }
        ]
        parser_state = "BLOCKED_UNSUPPORTED_SIZE_EXACT_BYTES_PRESERVED"
        encoding = None
    else:
        documents, facts, parser_state, encoding = _extract_source(
            path, relative_path, data, lane
        )
    source_sha256 = sha256_bytes(data)
    cursor = connection.execute(
        """
        INSERT INTO source_registry(
            path, size_bytes, sha256, mime_type, extension, encoding,
            parser_state, exact_bytes, registered_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            relative_path,
            len(data),
            source_sha256,
            mimetypes.guess_type(relative_path)[0] or "application/octet-stream",
            path.suffix.lower(),
            encoding,
            parser_state,
            data,
            registered_at,
        ),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("SQLite did not return a source row ID.")
    source_id = int(cursor.lastrowid)
    for fact in facts:
        connection.execute(
            """
            INSERT INTO structured_fact(source_id, kind, locator, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                source_id,
                fact["kind"],
                fact["locator"],
                json.dumps(fact["payload"], sort_keys=True, separators=(",", ":")),
            ),
        )
        if fact["kind"] in lane.schema_contract:
            table = fact["kind"]
            # ``table`` is selected only from the immutable validated registry.
            insert_fact_sql = (
                f"INSERT INTO {table}(source_id, locator, payload_json) "  # nosec B608
                "VALUES (?, ?, ?)"
            )
            connection.execute(
                insert_fact_sql,
                (
                    source_id,
                    fact["locator"],
                    json.dumps(fact["payload"], sort_keys=True, separators=(",", ":")),
                ),
            )
    for document in documents:
        text = str(document.get("text") or "")
        for ordinal, char_start, block in _chunks(text):
            chunk_sha256 = sha256_bytes(block.encode("utf-8"))
            cas_cursor = connection.execute(
                """
                INSERT OR IGNORE INTO chunk_content_cas(
                    sha256, size_bytes, text_content, first_seen_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    chunk_sha256,
                    len(block.encode("utf-8")),
                    block,
                    registered_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO chunk_index(
                    source_id, locator, ordinal, char_start, char_end,
                    text_content, sha256, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    str(document["locator"]),
                    ordinal,
                    char_start,
                    char_start + len(block),
                    block,
                    chunk_sha256,
                    json.dumps(
                        document.get("metadata") or {},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO chunk_history(
                    source_path, source_sha256, locator, ordinal, chunk_sha256,
                    snapshot_ref, observed_at, content_reused
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    relative_path,
                    source_sha256,
                    str(document["locator"]),
                    ordinal,
                    chunk_sha256,
                    snapshot_ref,
                    registered_at,
                    int(not bool(cas_cursor.rowcount)),
                ),
            )
    return source_id, parser_state


def _rebuild_retrieval(connection: sqlite3.Connection, lane: LaneDefinition) -> None:
    fts = lane.fts_table
    connection.execute(f"DELETE FROM {fts}")  # nosec B608
    # ``fts`` is regex-validated immutable registry data.
    insert_fts_sql = (
        f"INSERT INTO {fts}(path, locator, text_content, chunk_id) "  # nosec B608
        "SELECT s.path, c.locator, c.text_content, c.chunk_id "
        "FROM chunk_index c JOIN source_registry s ON s.source_id = c.source_id "
        "ORDER BY c.chunk_id"
    )
    connection.execute(insert_fts_sql)
    connection.execute("DELETE FROM tfidf_vector")
    connection.execute("DELETE FROM tfidf_term")
    rows = connection.execute(
        "SELECT chunk_id, text_content FROM chunk_index ORDER BY chunk_id"
    ).fetchall()
    document_count = len(rows)
    counters: dict[int, Counter[str]] = {}
    document_frequency: Counter[str] = Counter()
    for row in rows:
        counter = Counter(
            token.lower() for token in _TOKEN_RE.findall(row["text_content"])
        )
        counters[int(row["chunk_id"])] = counter
        document_frequency.update(counter.keys())
    idf_values: dict[str, float] = {}
    for term in sorted(document_frequency):
        df = document_frequency[term]
        idf = math.log((1 + document_count) / (1 + df)) + 1.0
        idf_values[term] = idf
        connection.execute(
            """
            INSERT INTO tfidf_term(term, document_frequency, document_count, idf)
            VALUES (?, ?, ?, ?)
            """,
            (term, df, document_count, idf),
        )
    for chunk_id, counter in counters.items():
        token_count = sum(counter.values())
        ranked = sorted(
            counter.items(),
            key=lambda item: (
                -(item[1] / max(token_count, 1)) * idf_values[item[0]],
                item[0],
            ),
        )[:TFIDF_TERMS_PER_CHUNK]
        for term, count in ranked:
            tf = count / max(token_count, 1)
            connection.execute(
                """
                INSERT INTO tfidf_vector(
                    chunk_id, term, term_count, token_count, tf, tfidf
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (chunk_id, term, count, token_count, tf, tf * idf_values[term]),
            )


def _validate_lane_database(path: Path, lane: LaneDefinition) -> dict[str, Any]:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    foreign_keys = [dict(row) for row in connection.execute("PRAGMA foreign_key_check")]
    schema = connection.execute(
        "SELECT value FROM lane_meta WHERE key='schema_version'"
    ).fetchone()
    counts = {
        table: int(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # nosec B608
        )
        for table in (
            "source_registry",
            "source_tombstone",
            "chunk_index",
            "structured_fact",
            "tfidf_term",
            "tfidf_vector",
            "refresh_receipt",
        )
    }
    fts_count = int(
        connection.execute(f"SELECT COUNT(*) FROM {lane.fts_table}").fetchone()[0]  # nosec B608
    )
    connection.close()
    valid = (
        integrity == ["ok"]
        and not foreign_keys
        and schema is not None
        and schema[0] == LANE_SCHEMA_VERSION
        and fts_count == counts["chunk_index"]
    )
    return {
        "status": "PASS" if valid else "FAIL",
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "schema_version": schema[0] if schema else None,
        "counts": counts,
        "fts_rows": fts_count,
        "valid": valid,
    }


def _source_index(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        row["path"]: {
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "parser_state": row["parser_state"],
        }
        for row in connection.execute(
            "SELECT path, sha256, size_bytes, parser_state FROM source_registry"
        )
    }


def _current_index(root: Path, paths: Iterable[str]) -> dict[str, dict[str, Any]]:
    return {
        relative: {
            "sha256": sha256_file(root / Path(relative)),
            "size_bytes": (root / Path(relative)).stat().st_size,
        }
        for relative in sorted(paths)
    }


def _classify(
    prior: dict[str, dict[str, Any]],
    current: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    unchanged = [
        {"path": path, **current[path]}
        for path in sorted(prior.keys() & current.keys())
        if prior[path]["sha256"] == current[path]["sha256"]
    ]
    changed = [
        {
            "path": path,
            "prior_sha256": prior[path]["sha256"],
            "current_sha256": current[path]["sha256"],
            "prior_size_bytes": prior[path]["size_bytes"],
            "current_size_bytes": current[path]["size_bytes"],
        }
        for path in sorted(prior.keys() & current.keys())
        if prior[path]["sha256"] != current[path]["sha256"]
    ]
    added = [
        {"path": path, **current[path]}
        for path in sorted(current.keys() - prior.keys())
    ]
    removed = [
        {"path": path, **prior[path]} for path in sorted(prior.keys() - current.keys())
    ]
    return {
        "UNCHANGED_REUSE": unchanged,
        "CHANGED_REBUILD": changed,
        "NEW_REGISTER": added,
        "REMOVED_TOMBSTONE": removed,
        "BLOCKED_UNSUPPORTED": [],
    }


_TOPOLOGY_CORE_TABLES = {
    "lane_meta",
    "lane_pointer",
    "source_registry",
    "source_tombstone",
    "chunk_index",
    "chunk_content_cas",
    "chunk_history",
    "structured_fact",
    "parser_capability",
    "tfidf_term",
    "tfidf_vector",
    "refresh_receipt",
    "mutation_receipt",
}


def _topology_text(value: Any, *, limit: int = 96) -> str:
    normalized = re.sub(
        r"\s+",
        " ",
        redact_text(str(value or "")).replace("\\", "/"),
    ).strip()
    if len(normalized) > limit:
        return normalized[: max(1, limit - 3)].rstrip() + "..."
    return normalized


def _mmd_label(value: Any) -> str:
    return (
        _topology_text(value, limit=180)
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "&#124;")
    )


def _dot_label(value: Any) -> str:
    return (
        _topology_text(value, limit=180)
        .replace("\\", "\\\\")
        .replace('"', '\\"')
    )


class _TopologyGraph:
    """Emit one deterministic semantic graph to both Mermaid and DOT."""

    _DOT_STYLE: ClassVar[dict[str, str]] = {
        "root": 'fillcolor="#101828",fontcolor="white",color="#101828"',
        "source": 'fillcolor="#edf5ff",color="#125cdd"',
        "semantic": 'fillcolor="#f0ebff",color="#7147c7"',
        "retrieval": 'fillcolor="#eaf8f1",color="#24805c"',
        "git": 'fillcolor="#fff7e7",color="#c88722"',
        "lifecycle": 'fillcolor="#fff1f0",color="#ba4236"',
        "output": 'fillcolor="#f7f9fc",color="#667085"',
        "warn": 'fillcolor="#fff7e7",color="#c88722"',
    }

    def __init__(self, name: str, *, direction: str = "TB") -> None:
        self.mmd = [
            f"flowchart {direction}",
            "    classDef root fill:#101828,stroke:#101828,color:#fff,stroke-width:2px;",
            "    classDef source fill:#edf5ff,stroke:#125cdd,color:#101828;",
            "    classDef semantic fill:#f0ebff,stroke:#7147c7,color:#101828;",
            "    classDef retrieval fill:#eaf8f1,stroke:#24805c,color:#101828;",
            "    classDef git fill:#fff7e7,stroke:#c88722,color:#101828;",
            "    classDef lifecycle fill:#fff1f0,stroke:#ba4236,color:#101828;",
            "    classDef output fill:#f7f9fc,stroke:#667085,color:#101828;",
            "    classDef warn fill:#fff7e7,stroke:#c88722,color:#101828;",
        ]
        self.dot = [
            f"digraph {name} {{",
            f'  rankdir="{direction}";',
            '  graph [fontname="Arial",bgcolor="white"];',
            '  node [shape="box",style="rounded,filled",fontname="Arial",color="#667085"];',
            '  edge [fontname="Arial",color="#667085"];',
        ]

    def begin(self, node_id: str, label: str, *, direction: str = "TB") -> None:
        self.mmd.extend(
            [f'    subgraph {node_id}["{_mmd_label(label)}"]', f"        direction {direction}"]
        )
        self.dot.append(f'  subgraph cluster_{node_id.lower()} {{ label="{_dot_label(label)}";')

    def end(self) -> None:
        self.mmd.append("    end")
        self.dot.append("  }")

    def node(self, node_id: str, label: str, kind: str) -> None:
        mmd_label = "<br/>".join(_mmd_label(part) for part in str(label).split("\n"))
        dot_label = "\\n".join(_dot_label(part) for part in str(label).split("\n"))
        self.mmd.append(f'        {node_id}["{mmd_label}"]:::{kind}')
        style = self._DOT_STYLE[kind]
        self.dot.append(f'    {node_id} [label="{dot_label}",{style}];')

    def edge(self, source: str, target: str, label: str | None = None) -> None:
        if label:
            self.mmd.append(
                f"        {source} -->|{_mmd_label(label)}| {target}"
            )
            self.dot.append(
                f'    {source} -> {target} [label="{_dot_label(label)}"];'
            )
        else:
            self.mmd.append(f"        {source} --> {target}")
            self.dot.append(f"    {source} -> {target};")

    def finish(self) -> tuple[str, str]:
        return "\n".join(self.mmd) + "\n", "\n".join([*self.dot, "}"]) + "\n"


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", table):
        return 0
    try:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])  # nosec B608
    except sqlite3.DatabaseError:
        return 0


def _fact_display(kind: str, locator: str, payload_json: str) -> str:
    try:
        payload = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if kind == "code_symbol":
        return str(payload.get("qualified_name") or payload.get("name") or locator)
    if kind == "code_import":
        imported = payload.get("imported_name")
        return f'{payload.get("module") or locator}{" : " + str(imported) if imported else ""}'
    if kind == "code_route":
        return f'{payload.get("method") or "ROUTE"} {payload.get("path_pattern") or locator}'
    if kind == "code_dependency":
        return f'{payload.get("name") or locator} {payload.get("constraint_text") or ""}'.strip()
    for key in (
        "text",
        "title",
        "name",
        "path",
        "question",
        "finding",
        "decision",
        "table",
        "sheet",
        "route",
    ):
        if payload.get(key):
            return str(payload[key])
    return locator


def _lane_topology(
    lane: LaneDefinition,
    database_path: Path,
    classification: dict[str, Any],
) -> tuple[str, str]:
    connection = sqlite3.connect(
        f"file:{database_path.resolve().as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    graph = _TopologyGraph(f"lane_{lane.canonical_lane_id}")
    sources = _table_count(connection, "source_registry")
    chunks = _table_count(connection, "chunk_index")
    facts = _table_count(connection, "structured_fact")
    graph.node(
        "LANE_ROOT",
        f"{lane.display_label}\n{sources} sources | {chunks} chunks | {facts} structured facts",
        "root",
    )

    graph.begin("SOURCE_INTAKE", "1. Source intake and exact-byte registry")
    graph.node("SOURCE_REG", f"source_registry\nrows={sources} | SHA-256 + parser state", "source")
    graph.edge("LANE_ROOT", "SOURCE_REG")
    extension_rows = connection.execute(
        """
        SELECT CASE WHEN extension='' THEN '[no extension]' ELSE extension END AS extension,
               COUNT(*) AS count
        FROM source_registry GROUP BY extension ORDER BY count DESC, extension LIMIT 8
        """
    ).fetchall()
    if extension_rows:
        for index, row in enumerate(extension_rows):
            node = f"SOURCE_TYPE_{index}"
            graph.node(node, f'{row["extension"]}\n{row["count"]} sources', "source")
            graph.edge("SOURCE_REG", node)
    else:
        graph.node("SOURCE_EMPTY", "schema ready\nno routed source in this build", "warn")
        graph.edge("SOURCE_REG", "SOURCE_EMPTY")
    graph.end()

    fact_counts = {
        str(row["kind"]): int(row["count"])
        for row in connection.execute(
            "SELECT kind, COUNT(*) AS count FROM structured_fact GROUP BY kind ORDER BY kind"
        )
    }
    lane_tables = [
        table
        for table in lane.schema_contract
        if table not in _TOPOLOGY_CORE_TABLES and table != lane.fts_table
    ]
    graph.begin("SEMANTIC_MODEL", "2. Lane-specific semantic model from SQLite")
    graph.node(
        "FACT_INDEX",
        f"structured_fact\nrows={facts} | kinds={len(fact_counts)}",
        "semantic",
    )
    graph.edge("SOURCE_REG", "FACT_INDEX")
    for index, table in enumerate(lane_tables):
        node = f"SCHEMA_{index}"
        graph.node(node, f"{table}\nrows={_table_count(connection, table)}", "semantic")
        graph.edge("FACT_INDEX", node, "materializes")

    if fact_counts:
        sampled_kinds = sorted(fact_counts, key=lambda item: (-fact_counts[item], item))[:10]
        for index, kind in enumerate(sampled_kinds):
            kind_node = f"FACT_KIND_{index}"
            graph.node(kind_node, f"{kind}\nrows={fact_counts[kind]}", "semantic")
            graph.edge("FACT_INDEX", kind_node)
            sample_rows = connection.execute(
                """
                SELECT locator, payload_json FROM structured_fact
                WHERE kind=? ORDER BY locator, fact_id LIMIT 2
                """,
                (kind,),
            ).fetchall()
            for sample_index, row in enumerate(sample_rows):
                sample_node = f"FACT_SAMPLE_{index}_{sample_index}"
                graph.node(
                    sample_node,
                    _fact_display(kind, str(row["locator"]), str(row["payload_json"])),
                    "semantic",
                )
                graph.edge(kind_node, sample_node, "sample")
    else:
        graph.node("FACT_EMPTY", "no semantic rows yet\nlane schema remains explicit", "warn")
        graph.edge("FACT_INDEX", "FACT_EMPTY")
    graph.end()

    if lane.canonical_lane_id in PRIMARY_CODE_LANES:
        graph.begin("CODE_SNAPSHOT", "3. Code snapshot relationships")
        code_nodes: dict[str, str] = {}
        for index, kind in enumerate(("code_symbol", "code_import", "code_route", "code_dependency")):
            node = f"CODE_{index}"
            code_nodes[kind] = node
            graph.node(node, f"{kind}\nrows={fact_counts.get(kind, 0)}", "semantic")
            graph.edge("FACT_INDEX", node)
        graph.edge(code_nodes["code_route"], code_nodes["code_symbol"], "handler")
        graph.edge(code_nodes["code_import"], code_nodes["code_dependency"], "resolves")
        graph.end()

        graph.begin("GIT_LINEAGE", "4. Full Git history and content-addressed reuse")
        git_tables = (
            "git_commit_registry",
            "git_commit_parent",
            "git_ref_registry",
            "git_blob_cas",
            "git_content_chunk_cas",
            "git_chunk_occurrence",
            "git_file_change",
            "git_history_fts",
        )
        previous = "LANE_ROOT"
        for index, table in enumerate(git_tables):
            node = f"GIT_{index}"
            graph.node(node, f"{table}\nrows={_table_count(connection, table)}", "git")
            graph.edge(previous, node)
            previous = node
        commit_rows = connection.execute(
            "SELECT commit_sha, message FROM git_commit_registry ORDER BY ordinal LIMIT 4"
        ).fetchall()
        for index, row in enumerate(commit_rows):
            node = f"GIT_COMMIT_{index}"
            graph.node(
                node,
                f'{str(row["commit_sha"])[:12]}\n{_topology_text(row["message"], limit=72)}',
                "git",
            )
            graph.edge("GIT_0", node, "commit sample")
        graph.end()

    section_number = 5 if lane.canonical_lane_id in PRIMARY_CODE_LANES else 3
    graph.begin("RETRIEVAL", f"{section_number}. Retrieval and changed-section reuse")
    retrieval = (
        ("CHUNK_INDEX", "chunk_index", chunks),
        ("CHUNK_CAS", "chunk_content_cas", _table_count(connection, "chunk_content_cas")),
        ("CHUNK_HISTORY", "chunk_history", _table_count(connection, "chunk_history")),
        ("FTS", lane.fts_table, _table_count(connection, lane.fts_table)),
        ("TFIDF", "tfidf_vector", _table_count(connection, "tfidf_vector")),
    )
    previous = "FACT_INDEX"
    for node, table, count in retrieval:
        graph.node(node, f"{table}\nrows={count}", "retrieval")
        graph.edge(previous, node)
        previous = node
    graph.end()

    section_number += 1
    graph.begin("LIFECYCLE", f"{section_number}. Refresh, pointer, and mutation evidence")
    refresh_summary = " | ".join(
        f"{key.lower()}={len(value) if isinstance(value, (list, dict)) else value}"
        for key, value in classification.items()
    )
    graph.node("REFRESH", f"refresh classification\n{refresh_summary}", "lifecycle")
    pointer = connection.execute(
        "SELECT pointer_value, generation FROM lane_pointer WHERE pointer_kind='entered_from'"
    ).fetchone()
    proposed = connection.execute(
        "SELECT value FROM lane_meta WHERE key='last_proposed_pv'"
    ).fetchone()
    graph.node(
        "POINTER",
        "lane pointer evidence\n"
        f'entered_from={pointer["pointer_value"] if pointer else "none"} | '
        f'generation={pointer["generation"] if pointer else 0}\n'
        f'proposed={proposed[0] if proposed else "unknown"}',
        "lifecycle",
    )
    graph.node(
        "MUTATION",
        "append-only change evidence\n"
        f'tombstones={_table_count(connection, "source_tombstone")} | '
        f'mutations={_table_count(connection, "mutation_receipt")}',
        "lifecycle",
    )
    graph.edge("TFIDF", "REFRESH")
    graph.edge("REFRESH", "POINTER")
    graph.edge("REFRESH", "MUTATION")
    graph.end()

    section_number += 1
    graph.begin("OUTPUTS", f"{section_number}. Inspectable lane package")
    graph.node("SQLITE_OUT", f"{lane.sqlite_filename}\nSQLite/FK/FTS authority", "output")
    graph.node("MMD_OUT", f"{lane.mmd_filename}\nsemantic Mermaid authority", "output")
    graph.node("DOT_OUT", f"{lane.dot_filename}\nsemantic DOT authority", "output")
    graph.node("POINTER_OUT", "lane_pointer.json\ncandidate pointer evidence", "output")
    graph.node("RECEIPT_OUT", "refresh_receipt.json\nclassification + validation", "output")
    graph.edge("POINTER", "SQLITE_OUT")
    for node in ("MMD_OUT", "DOT_OUT", "POINTER_OUT", "RECEIPT_OUT"):
        graph.edge("SQLITE_OUT", node)
    graph.end()
    connection.close()
    return graph.finish()


def _lane_stable_files(lane: LaneDefinition) -> tuple[str, ...]:
    return (
        lane.sqlite_filename,
        lane.mmd_filename,
        lane.dot_filename,
        "tools.json",
    )


def _lane_evidence_files(lane: LaneDefinition) -> tuple[str, ...]:
    """Return every lane-level artifact whose bytes are externally auditable."""

    return (
        *_lane_stable_files(lane),
        "lane_pointer.json",
        "refresh_receipt.json",
    )


def _lane_required_files(lane: LaneDefinition) -> tuple[str, ...]:
    return (*_lane_evidence_files(lane), "lane_manifest.json")


def _prior_lane_topology_is_reconcilable(
    prior_lane: Path | None,
    lane: LaneDefinition,
) -> bool:
    """Reject inherited topology that predates the current SQLite contract."""

    if prior_lane is None:
        return False
    required = (
        prior_lane / lane.sqlite_filename,
        prior_lane / lane.mmd_filename,
        prior_lane / lane.dot_filename,
    )
    if not all(path.is_file() for path in required):
        return False
    try:
        report = reconcile_lane_topology(
            prior_lane,
            lane_id=lane.canonical_lane_id,
            mmd_filename=lane.mmd_filename,
            dot_filename=lane.dot_filename,
            sqlite_filename=lane.sqlite_filename,
        )
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.DatabaseError):
        return False
    return report.get("status") == "PASS"


def _build_one_lane(
    *,
    root: Path,
    output: Path,
    lane: LaneDefinition,
    paths: list[str],
    prior_lane: Path | None,
    parent_pv: str | None,
    proposed_pv: str,
    pointer_generation: int,
    recorded_at: str,
    history_enabled: bool,
    source_snapshot: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    tools = _tool_identity(lane)
    prior_db = prior_lane / lane.sqlite_filename if prior_lane else None
    prior_tools = (
        json.loads((prior_lane / "tools.json").read_text(encoding="utf-8"))
        if prior_lane and (prior_lane / "tools.json").is_file()
        else None
    )
    prior_index: dict[str, dict[str, Any]] = {}
    if prior_db and prior_db.is_file():
        prior_connection = sqlite3.connect(
            f"file:{prior_db.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        prior_connection.row_factory = sqlite3.Row
        prior_index = _source_index(prior_connection)
        prior_connection.close()
    current_index = {
        path: source_snapshot[path]
        for path in sorted(paths)
    }
    classification = _classify(prior_index, current_index)
    current_git_signature = git_history_signature(root) if history_enabled else None
    prior_git_signature = None
    if history_enabled and prior_db and prior_db.is_file():
        prior_signature_connection = sqlite3.connect(
            f"file:{prior_db.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        prior_signature_row = prior_signature_connection.execute(
            "SELECT value FROM lane_meta WHERE key='git_history_signature'"
        ).fetchone()
        prior_signature_connection.close()
        prior_git_signature = (
            str(prior_signature_row[0]) if prior_signature_row else None
        )
    git_history_changed = bool(
        history_enabled and current_git_signature != prior_git_signature
    )
    tool_changed = bool(prior_tools and prior_tools.get("sha256") != tools["sha256"])
    topology_rebuild_required = bool(
        prior_lane is not None
        and not _prior_lane_topology_is_reconcilable(prior_lane, lane)
    )
    changed = (
        prior_lane is None
        or tool_changed
        or git_history_changed
        or topology_rebuild_required
        or any(
            classification[key]
            for key in ("CHANGED_REBUILD", "NEW_REGISTER", "REMOVED_TOMBSTONE")
        )
    )
    db_path = output / lane.sqlite_filename

    if not changed and prior_lane:
        for filename in _lane_stable_files(lane):
            source = prior_lane / filename
            atomic_write_bytes(output / filename, source.read_bytes())
        build_mode = "UNCHANGED_REUSE"
        byte_reused = True
        validation = _validate_lane_database(db_path, lane)
        history_report: dict[str, Any] | None = (
            {
                "status": "UNCHANGED_REUSE",
                "signature": current_git_signature,
                "single_index_reuse": True,
            }
            if history_enabled
            else None
        )
    else:
        if prior_db and prior_db.is_file() and not tool_changed:
            atomic_write_bytes(db_path, prior_db.read_bytes())
            connection = _open_lane(db_path, lane, initialize=False)
            build_mode = "INCREMENTAL_REFRESH"
            for row in (
                classification["CHANGED_REBUILD"] + classification["REMOVED_TOMBSTONE"]
            ):
                source = connection.execute(
                    "SELECT source_id, sha256, size_bytes FROM source_registry WHERE path=?",
                    (row["path"],),
                ).fetchone()
                if source is None:
                    continue
                delete_fts_sql = (
                    f"DELETE FROM {lane.fts_table} WHERE chunk_id IN "  # nosec B608
                    "(SELECT chunk_id FROM chunk_index WHERE source_id=?)"
                )
                connection.execute(
                    delete_fts_sql,
                    (source["source_id"],),
                )
                connection.execute(
                    "DELETE FROM source_registry WHERE source_id=?",
                    (source["source_id"],),
                )
                connection.execute(
                    """
                    INSERT INTO mutation_receipt(
                        mutation_kind, source_path, prior_sha256, current_sha256,
                        recorded_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        "CHANGED_REBUILD"
                        if row in classification["CHANGED_REBUILD"]
                        else "REMOVED_TOMBSTONE",
                        row["path"],
                        source["sha256"],
                        row.get("current_sha256"),
                        recorded_at,
                    ),
                )
                if row in classification["REMOVED_TOMBSTONE"]:
                    connection.execute(
                        """
                        INSERT INTO source_tombstone(
                            path, prior_sha256, prior_size_bytes, removed_at, parent_pv
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            row["path"],
                            source["sha256"],
                            source["size_bytes"],
                            recorded_at,
                            parent_pv,
                        ),
                    )
            for row in (
                classification["CHANGED_REBUILD"] + classification["NEW_REGISTER"]
            ):
                _, parser_state = _insert_source(
                    connection,
                    lane,
                    root,
                    row["path"],
                    registered_at=recorded_at,
                    snapshot_ref=proposed_pv,
                )
                if parser_state.startswith(("BLOCKED", "PARSE_FAILED")):
                    classification["BLOCKED_UNSUPPORTED"].append(
                        {"path": row["path"], "parser_state": parser_state}
                    )
            connection.execute("DELETE FROM parser_capability")
        else:
            connection = _open_lane(db_path, lane, initialize=True)
            build_mode = "FULL_PV1" if parent_pv is None else "FULL_VALIDATION_FALLBACK"
            for relative_path in paths:
                _, parser_state = _insert_source(
                    connection,
                    lane,
                    root,
                    relative_path,
                    registered_at=recorded_at,
                    snapshot_ref=proposed_pv,
                )
                if parser_state.startswith(("BLOCKED", "PARSE_FAILED")):
                    classification["BLOCKED_UNSUPPORTED"].append(
                        {"path": relative_path, "parser_state": parser_state}
                    )
        history_report = (
            index_git_history(connection, root) if history_enabled else None
        )
        connection.executemany(
            """
            INSERT INTO parser_capability(capability, state, tool, detail)
            VALUES (?, ?, ?, ?)
            """,
            [
                (row["capability"], row["state"], row["tool"], row["detail"])
                for row in tools["capabilities"]
            ],
        )
        connection.execute(
            "INSERT OR REPLACE INTO lane_meta(key, value) VALUES (?, ?)",
            ("schema_version", LANE_SCHEMA_VERSION),
        )
        for key, value in (
            ("lane_id", lane.canonical_lane_id),
            ("parser_id", lane.parser_id),
            ("chunker_version", lane.chunker_version),
            ("tool_identity_sha256", tools["sha256"]),
            ("last_proposed_pv", proposed_pv),
            ("last_build_mode", build_mode),
            ("git_history_signature", current_git_signature or "NOT_APPLICABLE"),
        ):
            connection.execute(
                "INSERT OR REPLACE INTO lane_meta(key, value) VALUES (?, ?)",
                (key, str(value)),
            )
        connection.execute(
            """
            INSERT OR REPLACE INTO lane_pointer(
                pointer_kind, pointer_value, generation, recorded_at
            ) VALUES (?, ?, ?, ?)
            """,
            ("entered_from", parent_pv, pointer_generation, recorded_at),
        )
        _rebuild_retrieval(connection, lane)
        chunk_reuse = connection.execute(
            """
            SELECT
                COALESCE(SUM(content_reused), 0),
                COALESCE(SUM(CASE WHEN content_reused=0 THEN 1 ELSE 0 END), 0)
            FROM chunk_history WHERE snapshot_ref=?
            """,
            (proposed_pv,),
        ).fetchone()
        classification["CHANGED_SECTION_REUSED"] = int(chunk_reuse[0])
        classification["CHANGED_SECTION_REINDEXED"] = int(chunk_reuse[1])
        connection.execute(
            """
            INSERT INTO refresh_receipt(
                build_mode, parent_pv, proposed_pv, unchanged_reuse,
                changed_rebuild, new_register, removed_tombstone,
                blocked_unsupported, details_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                build_mode,
                parent_pv,
                proposed_pv,
                len(classification["UNCHANGED_REUSE"]),
                len(classification["CHANGED_REBUILD"]),
                len(classification["NEW_REGISTER"]),
                len(classification["REMOVED_TOMBSTONE"]),
                len(classification["BLOCKED_UNSUPPORTED"]),
                json.dumps(classification, sort_keys=True, separators=(",", ":")),
                recorded_at,
            ),
        )
        connection.commit()
        connection.execute("VACUUM")
        connection.close()
        mmd, dot = _lane_topology(lane, db_path, classification)
        atomic_write_bytes(output / lane.mmd_filename, mmd.encode("utf-8"))
        atomic_write_bytes(output / lane.dot_filename, dot.encode("utf-8"))
        atomic_write_json(output / "tools.json", tools)
        byte_reused = False
        validation = _validate_lane_database(db_path, lane)

    lane_pointer = {
        "schema": "evidence-lane.lane-pointer-evidence.v1",
        "lane_id": lane.canonical_lane_id,
        "entered_from": parent_pv,
        "proposed_pv": proposed_pv,
        "pointer_generation": pointer_generation,
        "independent_authority": False,
        "recorded_at": recorded_at,
    }
    atomic_write_json(output / "lane_pointer.json", lane_pointer)
    refresh_receipt = {
        "schema": "evidence-lane.lane-refresh-receipt.v1",
        "lane_id": lane.canonical_lane_id,
        "build_mode": build_mode,
        "classification": classification,
        "tool_identity_changed": tool_changed,
        "git_history_changed": git_history_changed,
        "topology_rebuild_required": topology_rebuild_required,
        "git_history": history_report,
        "stable_artifacts_byte_reused": byte_reused,
        "parent_pv": parent_pv,
        "proposed_pv": proposed_pv,
        "validation": validation,
        "recorded_at": recorded_at,
    }
    atomic_write_json(output / "refresh_receipt.json", refresh_receipt)
    stable_hashes = {
        filename: sha256_file(output / filename)
        for filename in _lane_stable_files(lane)
    }
    evidence_hashes = {
        filename: sha256_file(output / filename)
        for filename in _lane_evidence_files(lane)
    }
    lane_manifest = {
        "schema": "evidence-lane.lane-manifest.v2",
        "lane": lane.as_dict(),
        "build_mode": build_mode,
        "stable_artifacts": stable_hashes,
        "evidence_artifacts": evidence_hashes,
        "required_artifacts": list(_lane_required_files(lane)),
        "pointer_evidence": "lane_pointer.json",
        "refresh_receipt": "refresh_receipt.json",
        "validation": validation,
        "recorded_at": recorded_at,
    }
    atomic_write_json(output / "lane_manifest.json", lane_manifest)
    return {
        "lane_id": lane.canonical_lane_id,
        "build_mode": build_mode,
        "byte_reused": byte_reused,
        "classification": classification,
        "git_history": history_report,
        "topology_rebuild_required": topology_rebuild_required,
        "validation": validation,
        "stable_artifacts": stable_hashes,
    }


def _bundle_graph(reports: list[dict[str, Any]]) -> tuple[str, str]:
    graph = _TopologyGraph("evidence_lane_project", direction="TB")
    graph.node(
        "PROJECT_ROOT",
        f"Universal Evidence Lane project\n{len(reports)} canonical lane packages",
        "root",
    )

    graph.begin("CONTROL_PLANE", "1. Six public controls")
    controls = (
        ("BOOT", "Boot\nruntime + locked Flash + host/storage"),
        ("ROLLBACK", "Rollback\naccepted pointer only"),
        ("BUILD", "Build\nunaccepted candidate + HIL"),
        ("REFRESH", "Refresh\nchanged sections only"),
        ("MODE", "Mode\nordered sidecar intersections"),
        ("SOURCE_INTAKE", "Source Intake\nauto-detect + explicit override"),
    )
    for node, label in controls:
        graph.node(node, label, "source")
        graph.edge("PROJECT_ROOT", node)
    graph.end()

    graph.begin("PARALLEL_LANES", "2. Deterministic lane fan-out and join")
    graph.node(
        "ROUTER",
        "one source snapshot + route receipt\nChat Lineage always included",
        "source",
    )
    graph.node(
        "PARALLEL_POOL",
        f"bounded parallel compute\nworkers <= {MAX_PARALLEL_LANE_WORKERS}",
        "retrieval",
    )
    graph.node(
        "DETERMINISTIC_JOIN",
        "deterministic join\nall lane validations must pass",
        "lifecycle",
    )
    graph.edge("BOOT", "ROUTER")
    graph.edge("SOURCE_INTAKE", "ROUTER")
    graph.edge("MODE", "ROUTER")
    graph.edge("REFRESH", "PARALLEL_POOL")
    graph.edge("ROUTER", "PARALLEL_POOL")
    for index, report in enumerate(reports):
        lane = LANE_REGISTRY[report["lane_id"]]
        counts = report.get("validation", {}).get("counts", {})
        lane_node = f"LANE_{index}"
        graph.node(
            lane_node,
            f'{lane.display_label}\n{report["build_mode"]} | '
            f'{counts.get("source_registry", 0)} sources | '
            f'{counts.get("structured_fact", 0)} facts',
            "semantic",
        )
        graph.edge("PARALLEL_POOL", lane_node)
        graph.edge(lane_node, "DETERMINISTIC_JOIN")
    graph.end()

    graph.begin("ARTIFACT_CONTRACT", "3. Per-lane and project evidence contract")
    graph.node(
        "LANE_OUTPUTS",
        "each lane\nSQLite + MMD + DOT + pointer + refresh receipt",
        "output",
    )
    graph.node(
        "PROJECT_OUTPUTS",
        "project package\nmaster MMD/DOT + manifests + hashes + Exit Slip",
        "output",
    )
    graph.node(
        "LINEAGE_OUTPUT",
        "visible ChatLineage\nprompts + steers + output + tools + files + tests + hashes",
        "output",
    )
    graph.edge("DETERMINISTIC_JOIN", "LANE_OUTPUTS")
    graph.edge("LANE_OUTPUTS", "PROJECT_OUTPUTS")
    chat_index = next(
        (index for index, report in enumerate(reports) if report["lane_id"] == "chat_lineage"),
        None,
    )
    if chat_index is not None:
        graph.edge(f"LANE_{chat_index}", "LINEAGE_OUTPUT")
    graph.edge("LINEAGE_OUTPUT", "PROJECT_OUTPUTS")
    graph.end()

    graph.begin("SERIAL_AUTHORITY", "4. Serial candidate, HIL, Fuse, and pointer authority")
    graph.node(
        "CANDIDATE",
        "UNACCEPTED candidate\nimmutable package + validation evidence",
        "lifecycle",
    )
    graph.node(
        "HIL",
        "six-way HIL\nno implicit acceptance by continuation",
        "lifecycle",
    )
    graph.node("APPROVE", "exact APPROVE\nbound to displayed candidate", "lifecycle")
    graph.node("DELTA", "APPROVE_WITH_DELTA\nbounded correction task", "warn")
    graph.node("RESEARCH", "MORE_RESEARCH\nquestion + evidence wait", "warn")
    graph.node("ROLLBACK_PATH", "ROLLBACK\nexisting accepted PV only", "warn")
    graph.node("REJECT", "REJECT\nterminal candidate receipt", "warn")
    graph.node("FAIL", "FAIL\nfailed gate receipt", "warn")
    graph.node("FUSE", "Fuse\nCAS + exact decision receipt", "lifecycle")
    graph.node("ACCEPTED", "accepted PV pointer\nmonotonic generation", "root")
    graph.node(
        "STATE_TRAVEL",
        "State Travel\nuser-requested fresh-host recovery only",
        "output",
    )
    graph.edge("PROJECT_OUTPUTS", "CANDIDATE")
    graph.edge("BUILD", "CANDIDATE")
    graph.edge("CANDIDATE", "HIL")
    graph.edge("HIL", "APPROVE")
    graph.edge("HIL", "DELTA")
    graph.edge("HIL", "RESEARCH")
    graph.edge("HIL", "ROLLBACK_PATH")
    graph.edge("HIL", "REJECT")
    graph.edge("HIL", "FAIL")
    graph.edge("APPROVE", "FUSE")
    graph.edge("FUSE", "ACCEPTED")
    graph.edge("ROLLBACK", "ROLLBACK_PATH")
    graph.edge("ROLLBACK_PATH", "ACCEPTED")
    graph.edge("ACCEPTED", "STATE_TRAVEL", "conditional")
    graph.end()
    return graph.finish()


def _lane_source_binding(
    output: Path,
    routes: dict[str, str],
    source_snapshot: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Verify every routed source hash against its completed lane database."""

    observed: dict[str, str] = {}
    duplicates: list[str] = []
    for lane_id in CANONICAL_LANE_IDS:
        lane = LANE_REGISTRY[lane_id]
        database = output / lane_id / lane.sqlite_filename
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            for path, source_sha256 in connection.execute(
                "SELECT path,sha256 FROM source_registry ORDER BY path"
            ):
                normalized = str(path)
                if normalized in observed:
                    duplicates.append(normalized)
                observed[normalized] = str(source_sha256)
        finally:
            connection.close()
    expected = {
        path: str(source_snapshot[path]["sha256"]) for path in sorted(routes)
    }
    mismatches = sorted(
        path
        for path in expected.keys() | observed.keys()
        if expected.get(path) != observed.get(path)
    )
    valid = (
        not duplicates
        and not mismatches
        and set(routes) == set(source_snapshot)
    )
    return {
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "expected_source_count": len(expected),
        "observed_source_count": len(observed),
        "duplicate_source_count": len(set(duplicates)),
        "mismatch_count": len(mismatches),
        "binding_sha256": sha256_bytes(canonical_json_bytes(observed)),
    }


def build_lane_bundle(
    *,
    repository_root: str | Path,
    output_directory: str | Path,
    code_mode: str,
    parent_lane_bundle: str | Path | None,
    parent_pv: str | None,
    proposed_pv: str,
    pointer_generation: int,
    source_overrides: dict[str, str] | None = None,
    git_mode: str = "AUTO",
    max_lane_workers: int = MAX_PARALLEL_LANE_WORKERS,
) -> dict[str, Any]:
    """Build/Refresh lanes concurrently, then assemble one deterministic PV."""

    if code_mode not in PRIMARY_CODE_LANES:
        raise ValueError("code_mode must be github_code or local_code")
    root = Path(repository_root).resolve()
    if not 1 <= max_lane_workers <= MAX_PARALLEL_LANE_WORKERS:
        raise ValueError(
            f"max_lane_workers must be between 1 and {MAX_PARALLEL_LANE_WORKERS}."
        )
    git_arm = probe_git_arm(root, requested_mode=git_mode)
    git_history_available = bool(git_arm["history_index_enabled"])
    output = Path(output_directory).resolve()
    if output.exists():
        if any(output.iterdir()):
            raise ValueError("Lane bundle output must be empty.")
    else:
        output.mkdir(parents=True)
    parent = Path(parent_lane_bundle).resolve() if parent_lane_bundle else None
    recorded_at = utc_now()
    source_selection, source_rows, source_exclusions = governed_source_files(root)
    source_paths = [relative for relative, _ in source_rows]
    source_snapshot = _current_index(root, source_paths)
    source_snapshot_sha256 = sha256_bytes(canonical_json_bytes(source_snapshot))
    inherited_routes: dict[str, str] = {}
    if parent and (parent / "routes.json").is_file():
        prior_routes = json.loads((parent / "routes.json").read_text(encoding="utf-8"))
        inherited_routes = {
            path: lane_id
            for path, lane_id in prior_routes.get("routes", {}).items()
            if path in source_paths
        }
    effective_overrides = {**inherited_routes, **(source_overrides or {})}
    routes = route_batch(
        source_paths,
        code_mode=code_mode,
        overrides=effective_overrides,
    )
    by_lane: dict[str, list[str]] = {lane_id: [] for lane_id in CANONICAL_LANE_IDS}
    for relative, lane_id in routes.items():
        by_lane[lane_id].append(relative)

    # RapidOCR lazily imports NumPy/OpenCV and creates ONNX Runtime native
    # thread pools. On Windows, starting that cold runtime inside one lane
    # worker while the Git lane repeatedly creates subprocess pipe-reader
    # threads can starve both workers under an MCP stdio host. Initialize the
    # shared OCR engine once, before the lane pool, then retain the existing
    # lock around individual OCR calls. This is a dependency cold-start
    # barrier only; independent lane computation remains parallel below.
    prewarmed_dependencies: list[str] = []
    if by_lane["images_ocr"] or by_lane["pdf_ocr"]:
        prewarmed_dependencies.extend(prewarm_native_dependencies())

    atomic_write_json(
        output / "registry.json",
        {
            "schema": "evidence-lane.canonical-lane-registry.v1",
            "lane_count": len(CANONICAL_LANE_IDS),
            "lanes": catalog(),
            "code_mode": code_mode,
            "single_registry": True,
        },
    )
    atomic_write_json(
        output / "routes.json",
        {
            "schema": "evidence-lane.source-route-authority.v1",
            "parent_pv": parent_pv,
            "routes": routes,
            "source_policy": {
                "selection_mode": source_selection,
                "tracked_only": source_selection == "GIT_TRACKED_ONLY",
                "excluded_source_count": len(source_exclusions),
                "excluded_sources": source_exclusions,
            },
            "inherited_route_count": len(inherited_routes),
            "session_override_count": len(source_overrides or {}),
            "single_lane_per_source": True,
            "recorded_at": recorded_at,
        },
    )
    reports_by_lane: dict[str, dict[str, Any]] = {}
    effective_workers = min(max_lane_workers, len(CANONICAL_LANE_IDS))
    with ThreadPoolExecutor(
        max_workers=effective_workers,
        thread_name_prefix="evidence-lane-build",
    ) as executor:
        futures = {}
        for lane_id in CANONICAL_LANE_IDS:
            lane = LANE_REGISTRY[lane_id]
            prior_lane = (
                parent / lane_id if parent and (parent / lane_id).is_dir() else None
            )
            future = executor.submit(
                _build_one_lane,
                root=root,
                output=output / lane_id,
                lane=lane,
                paths=by_lane[lane_id],
                prior_lane=prior_lane,
                parent_pv=parent_pv,
                proposed_pv=proposed_pv,
                pointer_generation=pointer_generation,
                recorded_at=recorded_at,
                history_enabled=lane_id == code_mode and git_history_available,
                source_snapshot=source_snapshot,
            )
            futures[future] = lane_id
        for future in as_completed(futures):
            lane_id = futures[future]
            reports_by_lane[lane_id] = future.result()
    reports = [reports_by_lane[lane_id] for lane_id in CANONICAL_LANE_IDS]

    final_selection, final_rows, final_exclusions = governed_source_files(root)
    final_source_paths = [relative for relative, _ in final_rows]
    final_source_snapshot = _current_index(root, final_source_paths)
    final_source_snapshot_sha256 = sha256_bytes(
        canonical_json_bytes(final_source_snapshot)
    )
    if (
        final_source_snapshot != source_snapshot
        or final_selection != source_selection
        or final_exclusions != source_exclusions
    ):
        raise ValueError(
            "Repository source snapshot changed during parallel lane build; "
            "the partial output is not a candidate."
        )
    source_binding = _lane_source_binding(output, routes, source_snapshot)
    if not source_binding["valid"]:
        raise ValueError(
            "Completed lane databases do not bind the frozen source snapshot; "
            "the partial output is not a candidate."
        )
    execution_receipt = {
        "schema": "evidence-lane.parallel-lane-execution.v1",
        "single_writer": True,
        "linear_governance": True,
        "parallel_lane_compute": effective_workers > 1,
        "prewarmed_dependencies": prewarmed_dependencies,
        "worker_count": effective_workers,
        "submitted_lane_count": len(CANONICAL_LANE_IDS),
        "deterministic_assembly_order": list(CANONICAL_LANE_IDS),
        "barrier_status": "PASS",
        "source_snapshot_sha256": source_snapshot_sha256,
        "final_source_snapshot_sha256": final_source_snapshot_sha256,
        "source_snapshot_unchanged": True,
        "source_binding": source_binding,
        "source_policy": {
            "selection_mode": source_selection,
            "tracked_only": source_selection == "GIT_TRACKED_ONLY",
            "excluded_source_count": len(source_exclusions),
            "excluded_sources": source_exclusions,
            "policy_unchanged_during_build": True,
            "secrets_indexed": False,
            "env_files_indexed": False,
            "runtime_artifacts_indexed": False,
            "untracked_operational_files_indexed": False,
        },
        "serialized_authorities": [
            "chat_lineage_append",
            "hil_decision",
            "fuse",
            "accepted_pointer_move",
            "rollback",
        ],
        "recorded_at": recorded_at,
    }
    atomic_write_json(output / "execution_receipt.json", execution_receipt)
    mmd, dot = _bundle_graph(reports)
    atomic_write_bytes(output / "project_lane_topology.mmd", mmd.encode("utf-8"))
    atomic_write_bytes(output / "project_lane_topology.dot", dot.encode("utf-8"))
    summary = {
        "full_build_lanes": [
            row["lane_id"]
            for row in reports
            if row["build_mode"] in {"FULL_PV1", "FULL_VALIDATION_FALLBACK"}
        ],
        "incremental_lanes": [
            row["lane_id"]
            for row in reports
            if row["build_mode"] == "INCREMENTAL_REFRESH"
        ],
        "byte_reused_lanes": [row["lane_id"] for row in reports if row["byte_reused"]],
        "changed_sources": sum(
            len(row["classification"]["CHANGED_REBUILD"]) for row in reports
        ),
        "new_sources": sum(
            len(row["classification"]["NEW_REGISTER"]) for row in reports
        ),
        "removed_sources": sum(
            len(row["classification"]["REMOVED_TOMBSTONE"]) for row in reports
        ),
        "blocked_sources": sum(
            len(row["classification"]["BLOCKED_UNSUPPORTED"]) for row in reports
        ),
        "changed_sections_reused": sum(
            int(row["classification"].get("CHANGED_SECTION_REUSED", 0))
            for row in reports
        ),
        "changed_sections_reindexed": sum(
            int(row["classification"].get("CHANGED_SECTION_REINDEXED", 0))
            for row in reports
        ),
        "git_history": next(
            (row["git_history"] for row in reports if row.get("git_history")),
            None,
        ),
        "git_optional_arm": git_arm,
        "parallel_execution": execution_receipt,
    }
    manifest = {
        "schema": LANE_BUNDLE_SCHEMA,
        "parent_pv": parent_pv,
        "proposed_pv": proposed_pv,
        "pointer_generation": pointer_generation,
        "code_mode": code_mode,
        "git_optional_arm": git_arm,
        "pv1_only_full_build": True,
        "lane_count": len(reports),
        "source_count": len(source_paths),
        "source_routes_sha256": sha256_bytes(canonical_json_bytes(routes)),
        "source_snapshot_sha256": source_snapshot_sha256,
        "source_policy": execution_receipt["source_policy"],
        "parallel_execution": execution_receipt,
        "reports": reports,
        "summary": summary,
        "created_at": recorded_at,
    }
    atomic_write_json(output / "manifest.json", manifest)
    files = {
        path.relative_to(output).as_posix(): sha256_file(path)
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.json"}
    }
    atomic_write_json(
        output / "SHA256SUMS.json",
        {
            "schema": "evidence-lane.recursive-sha256.v1",
            "members": files,
            "member_count": len(files),
        },
    )
    manifest["bundle_sha256"] = sha256_bytes(canonical_json_bytes(files))
    manifest["member_count"] = len(files) + 2
    atomic_write_json(output / "manifest.json", manifest)
    return manifest


def validate_lane_bundle(directory: str | Path) -> dict[str, Any]:
    root = Path(directory).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    registry = json.loads((root / "registry.json").read_text(encoding="utf-8"))
    routes = json.loads((root / "routes.json").read_text(encoding="utf-8"))
    execution_path = root / "execution_receipt.json"
    execution = (
        json.loads(execution_path.read_text(encoding="utf-8"))
        if execution_path.is_file()
        else None
    )
    pre_v110_compatibility = bool(
        manifest.get("schema") == LANE_BUNDLE_SCHEMA
        and "source_policy" not in manifest
        and execution is not None
        and "source_policy" not in execution
    )
    checksums = json.loads((root / "SHA256SUMS.json").read_text(encoding="utf-8"))
    declared_members = checksums.get("members", {})
    actual_members = {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "SHA256SUMS.json"}
    }
    checksum_set_match = set(declared_members) == set(actual_members)
    checksum_mismatches = {
        name: {
            "declared": declared_members.get(name),
            "actual": actual_members.get(name),
        }
        for name in sorted(set(declared_members) | set(actual_members))
        if declared_members.get(name) != actual_members.get(name)
    }
    topology_reconciliation = reconcile_bundle_topology(root)
    topology_by_lane = {
        row["lane_id"]: row for row in topology_reconciliation["lanes"]
    }
    lane_reports: dict[str, Any] = {}
    lane_manifest_errors: dict[str, Any] = {}
    for lane_id in CANONICAL_LANE_IDS:
        lane = LANE_REGISTRY[lane_id]
        lane_root = root / lane_id
        lane_reports[lane_id] = _validate_lane_database(
            lane_root / lane.sqlite_filename, lane
        )
        lane_manifest = json.loads(
            (lane_root / "lane_manifest.json").read_text(encoding="utf-8")
        )
        lane_manifest_schema = lane_manifest.get("schema")
        legacy_manifest = lane_manifest_schema == "evidence-lane.lane-manifest.v1"
        strict_manifest = lane_manifest_schema == "evidence-lane.lane-manifest.v2"
        stable_hashes = {
            filename: sha256_file(lane_root / filename)
            for filename in _lane_stable_files(lane)
        }
        evidence_hashes = {
            filename: sha256_file(lane_root / filename)
            for filename in _lane_evidence_files(lane)
        }
        required_files = set(_lane_required_files(lane))
        actual_lane_files = {
            path.name for path in lane_root.iterdir() if path.is_file()
        }
        topology_report = topology_by_lane[lane_id]
        mmd_valid = (
            topology_report["structural"]["mermaid"]["status"] == "PASS"
        )
        dot_valid = topology_report["structural"]["dot"]["status"] == "PASS"
        if (
            not (legacy_manifest or strict_manifest)
            or lane_manifest.get("lane", {}).get("canonical_lane_id") != lane_id
            or lane_manifest.get("stable_artifacts") != stable_hashes
            or (
                strict_manifest
                and lane_manifest.get("evidence_artifacts") != evidence_hashes
            )
            or (
                strict_manifest
                and set(lane_manifest.get("required_artifacts", []))
                != required_files
            )
            or not required_files <= actual_lane_files
            or (not pre_v110_compatibility and not mmd_valid)
            or (not pre_v110_compatibility and not dot_valid)
            or (
                not pre_v110_compatibility
                and topology_report["status"] != "PASS"
            )
        ):
            lane_manifest_errors[lane_id] = {
                "schema": lane_manifest_schema,
                "legacy_compatibility_path": legacy_manifest,
                "lane_id": lane_manifest.get("lane", {}).get("canonical_lane_id"),
                "declared_stable_artifacts": lane_manifest.get("stable_artifacts"),
                "actual_stable_artifacts": stable_hashes,
                "declared_evidence_artifacts": lane_manifest.get(
                    "evidence_artifacts"
                ),
                "actual_evidence_artifacts": evidence_hashes,
                "declared_required_artifacts": lane_manifest.get(
                    "required_artifacts"
                ),
                "missing_required_artifacts": sorted(
                    required_files - actual_lane_files
                ),
                "mmd_valid": mmd_valid,
                "dot_valid": dot_valid,
                "topology_reconciliation": topology_report,
            }
    expected_registry_ids = list(CANONICAL_LANE_IDS)
    registry_ids = [row.get("canonical_lane_id") for row in registry.get("lanes", [])]
    bundle_sha256 = sha256_bytes(canonical_json_bytes(actual_members))
    route_values_valid = all(
        lane_id in LANE_REGISTRY for lane_id in routes.get("routes", {}).values()
    )
    legacy_execution_compatibility = (
        manifest.get("schema") == LEGACY_LANE_BUNDLE_SCHEMA
        and execution is None
        and "parallel_execution" not in manifest
        and "source_snapshot_sha256" not in manifest
    )
    modern_execution_valid = bool(
        execution
        and execution.get("schema")
        == "evidence-lane.parallel-lane-execution.v1"
        and execution.get("single_writer") is True
        and execution.get("linear_governance") is True
        and execution.get("barrier_status") == "PASS"
        and execution.get("source_snapshot_unchanged") is True
        and execution.get("source_snapshot_sha256")
        == execution.get("final_source_snapshot_sha256")
        and execution.get("source_binding", {}).get("valid") is True
        and execution.get("source_policy", {}).get("policy_unchanged_during_build")
        is True
        and execution.get("source_policy", {}).get("secrets_indexed") is False
        and execution.get("source_policy", {}).get("env_files_indexed") is False
        and execution.get("source_policy", {}).get("runtime_artifacts_indexed")
        is False
        and execution.get("source_policy", {}).get(
            "untracked_operational_files_indexed"
        )
        is False
        and execution.get("deterministic_assembly_order")
        == list(CANONICAL_LANE_IDS)
        and manifest.get("parallel_execution") == execution
        and manifest.get("source_policy") == execution.get("source_policy")
        and manifest.get("source_snapshot_sha256")
        == execution.get("source_snapshot_sha256")
    )
    pre_v110_execution_valid = bool(
        pre_v110_compatibility
        and execution
        and execution.get("schema")
        == "evidence-lane.parallel-lane-execution.v1"
        and execution.get("single_writer") is True
        and execution.get("linear_governance") is True
        and execution.get("barrier_status") == "PASS"
        and execution.get("source_snapshot_unchanged") is True
        and execution.get("source_snapshot_sha256")
        == execution.get("final_source_snapshot_sha256")
        and execution.get("source_binding", {}).get("valid") is True
        and execution.get("deterministic_assembly_order")
        == list(CANONICAL_LANE_IDS)
        and manifest.get("parallel_execution") == execution
        and manifest.get("source_snapshot_sha256")
        == execution.get("source_snapshot_sha256")
    )
    execution_valid = (
        legacy_execution_compatibility
        or pre_v110_execution_valid
        or modern_execution_valid
    )
    topology_valid = bool(
        pre_v110_compatibility
        or topology_reconciliation["status"] == "PASS"
    )
    valid = (
        manifest.get("schema")
        in {LEGACY_LANE_BUNDLE_SCHEMA, LANE_BUNDLE_SCHEMA}
        and checksums.get("schema") == "evidence-lane.recursive-sha256.v1"
        and checksum_set_match
        and not checksum_mismatches
        and checksums.get("member_count") == len(actual_members)
        and manifest.get("member_count") == len(actual_members) + 2
        and manifest.get("bundle_sha256") == bundle_sha256
        and registry.get("lane_count") == len(CANONICAL_LANE_IDS)
        and registry_ids == expected_registry_ids
        and routes.get("schema") == "evidence-lane.source-route-authority.v1"
        and routes.get("single_lane_per_source") is True
        and route_values_valid
        and manifest.get("source_count") == len(routes.get("routes", {}))
        and manifest.get("source_routes_sha256")
        == sha256_bytes(canonical_json_bytes(routes.get("routes", {})))
        and manifest.get("lane_count") == len(CANONICAL_LANE_IDS)
        and execution_valid
        and topology_valid
        and not lane_manifest_errors
        and all(report["valid"] for report in lane_reports.values())
    )
    return {
        "status": "PASS" if valid else "FAIL",
        "valid": valid,
        "lane_count": len(lane_reports),
        "lanes": lane_reports,
        "summary": manifest.get("summary"),
        "bundle_sha256": bundle_sha256,
        "declared_bundle_sha256": manifest.get("bundle_sha256"),
        "checksum_set_match": checksum_set_match,
        "checksum_mismatches": checksum_mismatches,
        "lane_manifest_errors": lane_manifest_errors,
        "source_routes_valid": route_values_valid,
        "parallel_execution_valid": execution_valid,
        "parallel_execution_legacy_compatibility": (
            legacy_execution_compatibility
        ),
        "pre_v110_compatibility": pre_v110_compatibility,
        "pre_v110_execution_valid": pre_v110_execution_valid,
        "source_policy_enforced": not pre_v110_compatibility,
        "topology_reconciliation_enforced": not pre_v110_compatibility,
        "topology_valid": topology_valid,
        "topology_reconciliation": topology_reconciliation,
    }

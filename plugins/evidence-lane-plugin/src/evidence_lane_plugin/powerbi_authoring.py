"""Exact model and PBIR member edits; legacy reports and binary model writes remain closed."""

import base64
import io
import zipfile
from pathlib import PurePosixPath

from .powerbi_json_schema import validate_document, validate_project_members
from .powerbi_parsers import (
    MAX_BYTES,
    MAX_MEMBER_BYTES,
    allowed_companion,
    digest,
    fail,
    json_document,
    member_name,
    package_members,
    parse_powerbi,
)

RESOURCES = {".png", ".jpg", ".jpeg", ".svg", ".pbiviz"}


def editable(part):
    path = PurePosixPath(part)
    return (
        path.suffix.lower() in {".bim", ".tmdl", ".pbip", ".pbir", ".pbism"}
        or (path.suffix.lower() == ".json" and "/definition/" in "/" + part.casefold())
        or (path.suffix.lower() in RESOURCES and "/staticresources/" in "/" + part.casefold())
    )


def edit_model(arguments):
    raw = base64.b64decode(arguments["content_base64"], validate=True)
    if digest(raw) != arguments["expected_sha256"]:
        fail("EDIT_INPUT_CHANGED")
    filename = arguments["logical_name"]
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in {".bim", ".tmdl", ".pbip", ".pbir", ".pbism", ".zip"}:
        fail("EDIT_FORMAT_UNSUPPORTED")
    packaged = extension == ".zip"
    members = package_members(raw) if packaged else {PurePosixPath(filename).name: raw}
    changed = {}
    for edit in arguments["replacements"]:
        part = member_name(edit["part"])
        if (
            part != edit["part"]
            or not editable(part)
            or not allowed_companion(part)
            or part in changed
        ):
            fail("EDIT_PART_INVALID")
        expected = edit["expected_sha256"]
        if (digest(members[part]) if part in members else None) != expected:
            fail("EDIT_PART_CHANGED")
        if not packaged and part not in members:
            fail("EDIT_PART_INVALID")
        if edit.get("content_base64") is not None:
            if PurePosixPath(part).suffix.lower() not in RESOURCES:
                fail("EDIT_BINARY_TYPE_INVALID")
            replacement = base64.b64decode(edit["content_base64"], validate=True)
        elif edit.get("content_utf8") is not None:
            replacement = edit["content_utf8"].encode("utf-8")
        else:
            if expected is None or not packaged:
                fail("EDIT_DELETION_INVALID")
            replacement = None
        if replacement is not None and len(replacement) > MAX_MEMBER_BYTES:
            fail("EDIT_BYTE_BUDGET")
        changed[part] = replacement

    if packaged:
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as original, zipfile.ZipFile(output, "w") as target:
            target.comment = original.comment
            for info in original.infolist():
                if info.is_dir():
                    target.writestr(info, original.read(info))
                    continue
                if info.filename in changed and changed[info.filename] is None:
                    continue
                target.writestr(info, changed.get(info.filename, original.read(info)))
            for name, replacement in sorted(changed.items()):
                if name not in members:
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    target.writestr(info, replacement)
        content = output.getvalue()
        after = package_members(content)
        if any(after.get(name) != old for name, old in members.items() if name not in changed):
            fail("EDIT_UNSELECTED_PART_CHANGED")
        if any(
            (part in after if value is None else after.get(part) != value)
            for part, value in changed.items()
        ):
            fail("EDIT_SELECTED_PART_MISMATCH")
        entrypoint = arguments["parse_options"]["entrypoint"]
        project_evidence = (
            validate_project_members(after, entrypoint)
            if entrypoint
            else {"project_closure_checked": False, "reason": "No project entrypoint was admitted."}
        )
    else:
        if len(changed) != 1:
            fail("EDIT_PART_INVALID")
        content = next(iter(changed.values()))
        if extension in {".pbip", ".pbir", ".pbism"}:
            validate_document(PurePosixPath(filename).name, json_document(content), required=True)
        project_evidence = {"project_closure_checked": False, "scope": "single_exact_document"}
    if len(content) > MAX_BYTES:
        fail("EDIT_BYTE_BUDGET")
    options = {**arguments["parse_options"], "inspect_models": True}
    facts = parse_powerbi(filename, content, **options)
    model_parts = {
        row["part"]
        for row in facts["native_evidence"]
        if row["engine"] == "Microsoft.AnalysisServices.TOM"
    }
    for part, replacement in changed.items():
        if replacement is None:
            continue
        suffix = PurePosixPath(part).suffix.lower()
        if suffix == ".bim" and part not in model_parts:
            fail("EDIT_MODEL_NOT_VALIDATED")
        if suffix == ".tmdl" and not any(
            PurePosixPath(part).is_relative_to(PurePosixPath(model).parent)
            for model in model_parts
            if PurePosixPath(model).suffix.casefold() == ".tmdl"
        ):
            fail("EDIT_MODEL_NOT_VALIDATED")
        if suffix == ".json":
            validate_document(part, json_document(replacement), required=True)
    return content, {
        "operation": "exact_model_report_edit",
        "edited_parts": sorted(changed),
        "added_parts": sorted(part for part in changed if part not in members),
        "deleted_parts": sorted(part for part, value in changed.items() if value is None),
        "untouched_members_byte_identical": True,
        "project_validation": project_evidence,
        "model_metadata_deserialized": bool(model_parts),
        "report_schema_validated": facts["fidelity"]["report_schema_validated"],
        "calculation_execution": False,
        "report_rendering": False,
    }

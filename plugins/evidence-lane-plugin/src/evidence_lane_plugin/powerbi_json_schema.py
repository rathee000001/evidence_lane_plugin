"""Pinned Microsoft Power BI schemas with a closed, offline reference registry."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path, PurePosixPath

from .errors import LaneError

ROOT = Path(__file__).parent / "contracts/powerbi-schemas"
PREFIX = "https://developer.microsoft.com/json-schemas/"
CATEGORIES = {
    "report": "report",
    "page": "page",
    "visualContainer": "visual",
    "bookmark": "bookmark",
    "bookmarksMetadata": "bookmark",
    "pagesMetadata": "page",
    "reportExtension": "expression",
    "versionMetadata": "report",
    "filterConfiguration": "filter",
    "formattingObjectDefinitions": "theme",
}


@lru_cache(maxsize=1)
def schema_bundle():
    from referencing import Registry, Resource
    from referencing.exceptions import NoSuchResource

    def blocked(uri):
        raise NoSuchResource(ref=uri)

    raw = (ROOT / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    if manifest.get("schema") != "evidence-lane.powerbi-json-schemas.v4":
        raise LaneError(
            "POWERBI_SCHEMA_BUNDLE_INVALID", "The packaged Power BI schema bundle is invalid."
        )
    rows = manifest["resources"]
    if not 1 <= len(rows) <= 256 or len({r["uri"] for r in rows}) != len(rows):
        raise LaneError(
            "POWERBI_SCHEMA_BUNDLE_INVALID", "The schema resource set is not bounded and distinct."
        )
    documents, resources = {}, []
    for row in rows:
        filename = row["sha256"] + ".json"
        if row["path"] != filename or not row["uri"].startswith(PREFIX):
            raise LaneError(
                "POWERBI_SCHEMA_BUNDLE_INVALID",
                "A schema resource is outside its pinned namespace.",
            )
        content = (ROOT / filename).read_bytes()
        if len(content) != row["bytes"] or hashlib.sha256(content).hexdigest() != row["sha256"]:
            raise LaneError(
                "POWERBI_SCHEMA_BUNDLE_CHANGED",
                "A packaged Power BI schema differs from its admitted hash.",
            )
        document = json.loads(content)
        documents[row["uri"]] = (document, row)
        resources.append((row["uri"], Resource.from_contents(document)))
    return (
        Registry(retrieve=blocked).with_resources(resources),
        documents,
        {
            "bundle_sha256": hashlib.sha256(raw).hexdigest(),
            "upstream_commit": manifest["upstream_commit"],
            "network_used": False,
        },
    )


def document_kind(part, document):
    uri = document.get("$schema", "")
    if isinstance(uri, str) and uri.startswith(PREFIX + "fabric/item/report/definition/"):
        pieces = uri.removeprefix(PREFIX).split("/")
        if len(pieces) >= 7:
            return CATEGORIES.get(pieces[4])
    name = PurePosixPath(part).name.casefold()
    return {
        "report.json": "report",
        "page.json": "page",
        "visual.json": "visual",
        "pages.json": "page",
        "bookmarks.json": "bookmark",
        "version.json": "report",
        "reportextensions.json": "expression",
    }.get(name)


def validate_document(part, document, *, required=False):
    from jsonschema import Draft7Validator
    from referencing.exceptions import Unresolvable

    registry, documents, evidence = schema_bundle()
    uri = document.get("$schema")
    if not isinstance(uri, str) or uri not in documents:
        if required:
            raise LaneError(
                "POWERBI_SCHEMA_UNSUPPORTED",
                "The selected Power BI document needs an exact bundled Microsoft schema version.",
            )
        return None
    schema, row = documents[uri]
    suffix, name = PurePosixPath(part).suffix.casefold(), PurePosixPath(part).name.casefold()
    expected = {
        ".pbip": "fabric/pbip/pbipProperties/",
        ".pbir": "fabric/item/report/definitionProperties/",
        ".pbism": "fabric/item/semanticModel/definitionProperties/",
    }.get(suffix)
    if suffix == ".json":
        category = {
            "report.json": "report",
            "page.json": "page",
            "visual.json": "visualContainer",
            "pages.json": "pagesMetadata",
            "bookmarks.json": "bookmarksMetadata",
            "version.json": "versionMetadata",
            "reportextensions.json": "reportExtension",
        }.get(name)
        expected = (
            "fabric/item/report/definition/" + category + "/"
            if category
            else "fabric/item/report/definition/"
        )
    if expected is None or not uri.startswith(PREFIX + expected):
        raise LaneError(
            "POWERBI_SCHEMA_KIND_MISMATCH",
            "The declared schema does not describe the selected Power BI file type.",
        )
    try:
        error = next(Draft7Validator(schema, registry=registry).iter_errors(document), None)
    except Unresolvable, RecursionError, ValueError:
        raise LaneError(
            "POWERBI_SCHEMA_REFERENCE_INVALID",
            "The report does not resolve against the closed schema bundle.",
        ) from None
    if error is not None:
        # Vendor validation messages can echo filters, connection values and source expressions.
        raise LaneError(
            "POWERBI_SCHEMA_VALIDATION_FAILED",
            "The selected Power BI document does not satisfy its pinned Microsoft JSON schema.",
        )
    return {"part": part, "schema_uri": uri, "schema_sha256": row["sha256"], **evidence}


def validate_project_members(members, entrypoint):
    """Require a complete authorable project/report set; never follow filesystem links."""
    import posixpath

    from .powerbi_parsers import fail, json_document, member_name

    entrypoint = member_name(entrypoint)
    if entrypoint not in members or PurePosixPath(entrypoint).suffix.casefold() not in {
        ".pbip",
        ".pbir",
        ".pbism",
        ".bim",
        ".tmdl",
    }:
        fail("GENERATION_ENTRYPOINT_INVALID")
    documents = {}
    for name, raw in members.items():
        suffix = PurePosixPath(name).suffix.casefold()
        if (
            suffix in {".pbip", ".pbir", ".pbism"}
            or suffix == ".json"
            and "/definition/" in "/" + name.casefold()
        ):
            value = json_document(raw)
            validate_document(name, value, required=True)
            documents[name] = value

    def require_relative(part, relative, suffix):
        if (
            not isinstance(relative, str)
            or PurePosixPath(relative).is_absolute()
            or ":" in relative
            or "\\" in relative
        ):
            fail("PROJECT_REFERENCE_INVALID")
        target = member_name(
            posixpath.normpath(posixpath.join(str(PurePosixPath(part).parent), relative, suffix))
        )
        if target not in members:
            fail("PROJECT_REFERENCE_MISSING")

    for part, value in documents.items():
        if part.casefold().endswith(".pbip"):
            if not value["artifacts"]:
                fail("PROJECT_REPORT_MISSING")
            for artifact in value["artifacts"]:
                require_relative(part, artifact["report"]["path"], "definition.pbir")
        elif part.casefold().endswith(".pbir"):
            parent = PurePosixPath(part).parent
            report = (parent / "definition/report.json").as_posix()
            version = (parent / "definition/version.json").as_posix()
            if report not in documents or version not in documents:
                fail("PBIR_DEFINITION_INCOMPLETE")
            by_path = value["datasetReference"].get("byPath")
            if by_path is not None:
                require_relative(part, by_path["path"], "definition.pbism")
        elif part.casefold().endswith(".pbism"):
            parent = PurePosixPath(part).parent
            if not any(
                (parent / suffix).as_posix() in members
                for suffix in ("model.bim", "definition/model.tmdl")
            ):
                fail("SEMANTIC_MODEL_INCOMPLETE")
    return {
        "project_closure_checked": True,
        "report_json_schema_validated": any(
            name.casefold().endswith(".pbir") for name in documents
        ),
        "validated_documents": sorted(documents),
        "rendering_verified": False,
        "external_references_opened": False,
    }

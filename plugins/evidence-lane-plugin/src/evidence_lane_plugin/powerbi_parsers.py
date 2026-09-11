"""Closed Power BI inputs, offline report schemas and attributed native model data."""

from __future__ import annotations

import hashlib
import io
import json
import math
import posixpath
import re
import stat
import zipfile
from collections import Counter
from pathlib import PurePosixPath

from .errors import LaneError
from .hashing import canonical_json_bytes

EXTENSIONS = {".pbip", ".pbir", ".pbism", ".pbix", ".pbit", ".bim", ".tmdl", ".zip"}
MAX_BYTES = 16_777_216
MAX_MEMBER_BYTES = 8_388_608
KINDS = (
    "project",
    "report",
    "page",
    "visual",
    "model",
    "table",
    "column",
    "measure",
    "relationship",
    "partition",
    "expression",
    "role",
    "hierarchy",
    "data_source",
    "perspective",
    "culture",
    "annotation",
    "bookmark",
    "filter",
    "theme",
    "model_metadata",
    "model_row",
    "reference",
    "package_member",
    "opaque",
)
PRIVATE = re.compile(r"password|passwd|token|secret|credential|connectionstring", re.IGNORECASE)
EXCLUDED = {".pbi", ".git", ".env", "credentials", "node_modules", "__pycache__"}
TMDL_COLLECTIONS = {
    "tables",
    "roles",
    "cultures",
    "perspectives",
    "expressions",
    "relationships",
    "functions",
}
RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.IGNORECASE)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def fail(code):
    raise LaneError(
        "POWERBI_" + code, "The Power BI input does not satisfy its bounded format contract."
    )


def member_name(value):
    if not isinstance(value, str):
        fail("PACKAGE_PATH_INVALID")
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not value
        or len(value) > 1000
        or path.is_absolute()
        or ".." in path.parts
        or any(character in value for character in ':<>|?*"')
        or any(ord(character) < 32 for character in value)
        or any(part.rstrip(". ") != part or RESERVED.match(part) for part in path.parts)
        or str(path) == "."
        or path.as_posix() != normalized.rstrip("/")
    ):
        fail("PACKAGE_PATH_INVALID")
    return path.as_posix()


def allowed_companion(value):
    path = PurePosixPath(member_name(value))
    return not any(part.casefold() in EXCLUDED for part in path.parts) and path.suffix.lower() in {
        ".pbip",
        ".pbir",
        ".pbism",
        ".json",
        ".bim",
        ".tmdl",
        ".png",
        ".jpg",
        ".jpeg",
        ".svg",
        ".pbiviz",
    }


def package_members(content):
    result, seen, total = {}, set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            if len(archive.infolist()) > 256:
                fail("PACKAGE_MEMBER_BUDGET")
            for info in archive.infolist():
                raw_name = info.orig_filename
                name = member_name(raw_name)
                if (
                    name.casefold() in seen
                    or stat.S_ISLNK(info.external_attr >> 16)
                    or info.flag_bits & 1
                    or raw_name.rstrip("/") != name
                ):
                    fail("PACKAGE_MEMBER_INVALID")
                seen.add(name.casefold())
                if info.is_dir():
                    continue
                total += info.file_size
                if total > MAX_BYTES or info.file_size > MAX_MEMBER_BYTES:
                    fail("PACKAGE_BYTE_BUDGET")
                with archive.open(info) as stream:
                    raw = stream.read(MAX_MEMBER_BYTES + 1)
                if len(raw) != info.file_size or len(raw) > MAX_MEMBER_BYTES:
                    fail("PACKAGE_BYTE_BUDGET")
                result[name] = raw
    except zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError:
        fail("PACKAGE_INVALID")
    return result


def bundle(members):
    if len(members) > 256 or sum(map(len, members.values())) > MAX_BYTES:
        fail("PACKAGE_BYTE_BUDGET")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(members.items()):
            info = zipfile.ZipInfo(member_name(name), date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw)
    raw = output.getvalue()
    if len(raw) > MAX_BYTES:
        fail("PACKAGE_BYTE_BUDGET")
    if package_members(raw) != members:
        fail("PACKAGE_BINDING")
    return raw


def decode_text(raw):
    try:
        encoding = (
            "utf-16"
            if raw.startswith((b"\xff\xfe", b"\xfe\xff"))
            else "utf-16-le"
            if raw[:100].count(b"\x00") > 10
            else "utf-8-sig"
        )
        return raw.decode(encoding)
    except UnicodeError:
        fail("TEXT_ENCODING_INVALID")


def json_document(raw):
    def distinct(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                fail("JSON_DUPLICATE_KEY")
            value[key] = item
        return value

    try:
        value = json.loads(
            decode_text(raw),
            object_pairs_hook=distinct,
            parse_constant=lambda value: fail("JSON_NONFINITE"),
        )
    except ValueError, RecursionError:
        fail("JSON_INVALID")
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop()
        count += 1
        if count > 100_000 or depth > 64:
            fail("JSON_NODE_BUDGET")
        if isinstance(item, float) and not math.isfinite(item):
            fail("JSON_NONFINITE")
        if isinstance(item, dict):
            stack.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            stack.extend((child, depth + 1) for child in item)
    if not isinstance(value, dict):
        fail("JSON_ROOT_INVALID")
    return value


def redacted(value):
    if isinstance(value, dict):
        return {
            key: "[redacted]" if PRIVATE.search(key) else redacted(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redacted(item) for item in value]
    return value


def object_rows(value, key):
    values = value.get(key, [])
    if not isinstance(values, list) or any(not isinstance(row, dict) for row in values):
        fail("METADATA_COLLECTION_INVALID")
    return values


def parse_powerbi(
    filename, content, *, entrypoint=None, inspect_models=True, max_rows_per_table=20
):
    suffix = PurePosixPath(filename).suffix.lower()
    if (
        suffix not in EXTENSIONS
        or len(content) > MAX_BYTES
        or type(max_rows_per_table) is not int
        or not 0 <= max_rows_per_table <= 1000
    ):
        fail("INPUT_FORMAT_OR_BUDGET")
    packaged = suffix in {".pbix", ".pbit", ".zip"}
    members = package_members(content) if packaged else {PurePosixPath(filename).name: content}
    if entrypoint is not None and member_name(entrypoint) not in members:
        fail("ENTRYPOINT_MISSING")
    if suffix == ".zip" and (
        any(not allowed_companion(name) for name in members)
        or not any(
            PurePosixPath(name).suffix.lower() in {".pbip", ".pbir", ".pbism", ".bim", ".tmdl"}
            for name in members
        )
    ):
        fail("PROJECT_ARCHIVE_INVALID")
    if suffix in {".pbix", ".pbit"} and not any(
        name.casefold()
        in {"report/layout", "report/definition/report.json", "datamodel", "datamodelschema"}
        for name in members
    ):
        fail("PACKAGE_CONTENT_UNRECOGNIZED")
    items, counts, evidence, consumed_models = [], Counter(), [], set()
    schemas, unvalidated, pbix_rows, pbix_metadata = [], [], False, False
    limitations = [
        "JSON schema validation and model/data extraction do not prove rendered report layout or evaluated DAX.",
        "References are resolved only against explicitly admitted files; external connections are never opened.",
        "PBIT/PBIX rewriting and PBIX-to-PBIP conversion are not performed. Model rows are bounded stored-data samples.",
    ]

    def add(kind, part, locator, payload, basis="source_json"):
        if kind not in KINDS or not isinstance(payload, dict):
            fail("FACT_ITEM_INVALID")
        value = redacted(payload)
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if len(text) > 262144 or len(items) >= 50_000:
            fail("FACT_ITEM_BUDGET")
        # All source fields live below data. They cannot replace governed identifiers.
        aliases = {
            key: value[key]
            for key in ("name", "displayName", "table_name", "caption")
            if key in value
        }
        items.append(
            {
                **aliases,
                "data": value,
                "item_id": digest(canonical_json_bytes([part, kind, locator])),
                "kind": kind,
                "part": part,
                "ordinal": counts[kind],
                "locator": locator,
                "locator_basis": basis,
                "text": text,
            }
        )
        counts[kind] += 1

    def checked_document(part, raw):
        from .powerbi_json_schema import validate_document

        data = json_document(raw)
        observed = validate_document(part, data)
        if observed is None:
            unvalidated.append(part)
        else:
            schemas.append(observed)
        return data

    def reference(part, locator, relative, target_name=None):
        target = None
        if (
            isinstance(relative, str)
            and not PurePosixPath(relative.replace("\\", "/")).is_absolute()
            and ":" not in relative
        ):
            candidate = posixpath.normpath(
                posixpath.join(str(PurePosixPath(part).parent), relative.replace("\\", "/"))
            )
            if candidate != ".." and not candidate.startswith("../"):
                target = posixpath.join(candidate, target_name) if target_name else candidate
        add(
            "reference",
            part,
            locator,
            {
                "relative_path": relative,
                "target": target,
                "resolved_in_snapshot": target in members,
                "filesystem_followed": False,
            },
        )

    def model_items(part, database, basis):
        model = database.get("model", database)
        if not isinstance(model, dict):
            fail("MODEL_INVALID")
        collections = (
            "tables",
            "relationships",
            "expressions",
            "roles",
            "dataSources",
            "perspectives",
            "cultures",
            "annotations",
        )
        add(
            "model",
            part,
            "/model",
            {
                **{key: value for key, value in model.items() if key not in collections},
                "database_properties": {
                    key: value for key, value in database.items() if key != "model"
                },
            },
            basis,
        )
        for ordinal, table in enumerate(object_rows(model, "tables")):
            path = "/model/tables/" + str(ordinal)
            children = ("columns", "measures", "partitions", "hierarchies", "annotations")
            add(
                "table",
                part,
                path,
                {key: value for key, value in table.items() if key not in children},
                basis,
            )
            for collection, kind in (
                ("columns", "column"),
                ("measures", "measure"),
                ("partitions", "partition"),
                ("hierarchies", "hierarchy"),
                ("annotations", "annotation"),
            ):
                for n, value in enumerate(object_rows(table, collection)):
                    add(
                        kind,
                        part,
                        path + "/" + collection + "/" + str(n),
                        {**value, "table_name": table.get("name")},
                        basis,
                    )
        for collection, kind in (
            ("relationships", "relationship"),
            ("expressions", "expression"),
            ("roles", "role"),
            ("dataSources", "data_source"),
            ("perspectives", "perspective"),
            ("cultures", "culture"),
            ("annotations", "annotation"),
        ):
            for n, value in enumerate(object_rows(model, collection)):
                add(kind, part, "/model/" + collection + "/" + str(n), value, basis)

    tmdl_groups = {
        str(PurePosixPath(name).parent)
        for name in members
        if PurePosixPath(name).name.casefold() == "model.tmdl"
        and PurePosixPath(name).parent.name.casefold() not in TMDL_COLLECTIONS
    }
    if any(
        a != b and (a == "." or b.startswith(a + "/")) for a in tmdl_groups for b in tmdl_groups
    ):
        fail("TMDL_GROUP_OVERLAP")
    if inspect_models:
        from .powerbi_native import parse_native, parse_pbix

        for folder in sorted(tmdl_groups):
            group = {
                name: raw
                for name, raw in members.items()
                if PurePosixPath(name).suffix.lower() == ".tmdl"
                and (folder == "." or name.startswith(folder + "/"))
            }
            inputs = [
                (str(PurePosixPath(name).relative_to(folder)), raw)
                for name, raw in sorted(group.items())
            ]
            native, observed = parse_native("tmdl", inputs)
            part = next(
                name for name in group if PurePosixPath(name).name.casefold() == "model.tmdl"
            )
            model_items(part, native["database"], "native_tom_canonical_json")
            evidence.append({"part": part, **observed})
            consumed_models.update(group)
        if suffix == ".pbix" and "DataModel" in members:
            native, observed = parse_pbix(content, max_rows_per_table=max_rows_per_table)
            evidence.append({"part": "DataModel", **observed})
            pbix_metadata, pbix_rows = True, native["rows_decoded"]
            add(
                "model",
                "DataModel",
                "/",
                {"name": "Embedded PBIX model", "table_count": len(native["tables"])},
                "pbixray_response",
            )
            mapping = {
                "schema": "column",
                "relationships": "relationship",
                "rls": "role",
                "ols": "role",
                "power_query": "expression",
                "m_parameters": "expression",
                "dax_measures": "measure",
                "dax_columns": "expression",
                "dax_tables": "expression",
            }
            for collection, rows in native["metadata_collections"].items():
                for n, row in enumerate(rows):
                    alias = (
                        row.get("Name")
                        or row.get("ColumnName")
                        or row.get("MeasureName")
                        or row.get("TableName")
                    )
                    add(
                        mapping.get(collection, "model_metadata"),
                        "DataModel",
                        "/metadata_collections/" + collection + "/" + str(n),
                        {
                            **row,
                            "name": alias,
                            "table_name": row.get("TableName"),
                            "source_collection": collection,
                        },
                        "pbixray_response",
                    )
            for n, table in enumerate(native["tables"]):
                add(
                    "table",
                    "DataModel",
                    "/tables/" + str(n),
                    {key: value for key, value in table.items() if key != "rows"},
                    "pbixray_response",
                )
                for index, values in enumerate(table["rows"]):
                    add(
                        "model_row",
                        "DataModel",
                        "/tables/" + str(n) + "/rows/" + str(index),
                        {
                            "table_name": table["name"],
                            "columns": table["columns"],
                            "values": values,
                            "row_index": index,
                            "sample_limit": table["sample_limit"],
                            "has_more_rows": table["has_more_rows"],
                        },
                        "pbixray_response",
                    )
            consumed_models.add("DataModel")

    for part, raw in sorted(members.items()):
        path, lower = PurePosixPath(part), part.casefold()
        if packaged:
            add(
                "package_member",
                part,
                "/",
                {"name": part, "sha256": digest(raw), "bytes": len(raw)},
                "package_member",
            )
        if part in consumed_models:
            continue
        ext = path.suffix.lower()
        if ext == ".bim" or lower == "datamodelschema":
            database = json_document(raw)
            basis = "source_json"
            if inspect_models:
                native, observed = parse_native("bim", [(part, raw)])
                database, basis = native["database"], "native_tom_canonical_json"
                evidence.append({"part": part, **observed})
            model_items(part, database, basis)
        elif ext == ".pbip":
            data = checked_document(part, raw)
            if not isinstance(data.get("version"), str):
                fail("PROJECT_VERSION_INVALID")
            add(
                "project",
                part,
                "/",
                {key: value for key, value in data.items() if key != "artifacts"},
            )
            for n, artifact in enumerate(object_rows(data, "artifacts")):
                if isinstance(artifact.get("report"), dict):
                    reference(
                        part,
                        "/artifacts/" + str(n),
                        artifact["report"].get("path"),
                        "definition.pbir",
                    )
        elif ext == ".pbir":
            data = checked_document(part, raw)
            if not isinstance(data.get("version"), str) or not isinstance(
                data.get("datasetReference"), dict
            ):
                fail("REPORT_DEFINITION_INVALID")
            add("report", part, "/", data)
            by_path = data["datasetReference"].get("byPath", {})
            if isinstance(by_path, dict) and "path" in by_path:
                reference(part, "/datasetReference/byPath", by_path["path"], "definition.pbism")
        elif ext == ".pbism":
            data = checked_document(part, raw)
            if not isinstance(data.get("version"), str):
                fail("MODEL_VERSION_INVALID")
            add("model", part, "/", data)
        elif (
            lower == "report/layout"
            or path.name.casefold() == "report.json"
            and "/definition/" not in "/" + lower
        ):
            unvalidated.append(part)
            data = json_document(raw)
            add(
                "report",
                part,
                "/",
                {
                    **{key: value for key, value in data.items() if key != "sections"},
                    "legacy_format": True,
                },
            )
            for n, page in enumerate(object_rows(data, "sections")):
                locator = "/sections/" + str(n)
                add(
                    "page",
                    part,
                    locator,
                    {key: value for key, value in page.items() if key != "visualContainers"},
                )
                for v, visual in enumerate(object_rows(page, "visualContainers")):
                    add("visual", part, locator + "/visualContainers/" + str(v), visual)
        elif ext == ".json" and "/definition/" in "/" + lower:
            from .powerbi_json_schema import document_kind

            data = checked_document(part, raw)
            kind = document_kind(part, data)
            if kind:
                add(kind, part, "/", data)
                if isinstance(data.get("filterConfig"), dict):
                    for n, value in enumerate(object_rows(data["filterConfig"], "filters")):
                        add("filter", part, "/filterConfig/filters/" + str(n), value)
        elif lower == "connections":
            add("reference", part, "/", {**json_document(raw), "external_connection_opened": False})
        elif ext == ".tmdl":
            add(
                "opaque",
                part,
                "/",
                {
                    "name": part,
                    "sha256": digest(raw),
                    "bytes": len(raw),
                    "reason": "TMDL fragment retained; a complete model.tmdl group was not natively deserialized.",
                },
                "exact_bytes",
            )
        elif lower in {"datamodel", "datamashup"}:
            add(
                "opaque",
                part,
                "/",
                {
                    "name": part,
                    "sha256": digest(raw),
                    "bytes": len(raw),
                    "reason": "The selected inspection did not decode this binary member.",
                },
                "exact_bytes",
            )
    if len({row["item_id"] for row in items}) != len(items):
        fail("LOCATOR_COLLISION")
    if unvalidated:
        limitations.append(
            "Some project/report JSON files have no supported pinned schema; their JSON metadata alone was read."
        )
    body = {
        "schema": "evidence-lane.powerbi-facts.v4",
        "lane_id": "power_bi",
        "format": suffix,
        "items": items,
        "counts": dict(sorted(counts.items())),
        "features": {
            "package": packaged,
            "native_models_inspected": len(evidence),
            "pbix_model_metadata_read": pbix_metadata,
            "validated_json_documents": len(schemas),
        },
        "parse_options": {
            "entrypoint": entrypoint,
            "inspect_models": inspect_models,
            "max_rows_per_table": max_rows_per_table,
        },
        "fidelity": {
            "exact_source_bytes": True,
            "metadata_extracted": True,
            "model_metadata_deserialized": bool(evidence),
            "layout_verified": False,
            "report_schema_validated": any(
                "/fabric/item/report/definition/report/" in row["schema_uri"] for row in schemas
            )
            and not unvalidated,
            "dax_evaluated": False,
            "live_data_read": False,
            "compressed_model_data_read": pbix_rows,
            "bounded_stored_row_samples": pbix_rows,
            "pbix_conversion": False,
        },
        "limitations": limitations,
        "native_evidence": evidence,
        "schema_evidence": schemas,
        "schema_unvalidated_parts": unvalidated,
    }
    if len(canonical_json_bytes(body)) > MAX_BYTES:
        fail("FACT_BYTE_BUDGET")
    return body

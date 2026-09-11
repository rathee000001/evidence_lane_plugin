"""Owned Power BI workers; project references never cause automatic source reads."""

import base64
from pathlib import Path, PurePosixPath

from .hashing import canonical_json_bytes
from .powerbi_parsers import MAX_BYTES, allowed_companion, bundle, digest, fail, parse_powerbi
from .storage import reject_links


def source_bytes(path, limit):
    path = Path(path)
    reject_links(path, Path(path.anchor))
    before = path.stat()
    with path.open("rb") as stream:
        content = stream.read(limit + 1)
    after = path.stat()
    if len(content) > limit:
        fail("SOURCE_BYTE_BUDGET")
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        fail("SOURCE_CHANGED")
    return content


def encoded(
    filename, content, *, entrypoint=None, inspect_models=True, max_rows_per_table=20, evidence=None
):
    if len(content) > MAX_BYTES:
        fail("SOURCE_BYTE_BUDGET")
    facts = parse_powerbi(
        filename,
        content,
        entrypoint=entrypoint,
        inspect_models=inspect_models,
        max_rows_per_table=max_rows_per_table,
    )
    return {
        "filename": filename,
        "sha256": digest(content),
        "bytes": len(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
        "facts": facts,
        "evidence": {
            "source_bytes_mutated": False,
            "layout_verified": False,
            "native": facts["native_evidence"],
            **(evidence or {"operation": "closed_input_extraction"}),
        },
    }


def parse_file(arguments):
    members = {}
    paths, names = arguments["filenames"], arguments["names"]
    if not 1 <= len(paths) <= 128 or len(paths) != len(names) or len(set(names)) != len(names):
        fail("SOURCE_SET_INVALID")
    for path, name in zip(paths, names, strict=True):
        members[name] = source_bytes(path, arguments["max_file_bytes"])
        if sum(len(raw) for raw in members.values()) > MAX_BYTES:
            fail("SOURCE_BYTE_BUDGET")
    if arguments["entrypoint"] is not None:
        if any(not allowed_companion(name) for name in members):
            fail("COMPANION_FORMAT_INVALID")
        content = bundle(members)
    else:
        if len(members) != 1:
            fail("COMPANION_FORMAT_INVALID")
        content = next(iter(members.values()))
    return encoded(
        arguments["logical_name"],
        content,
        entrypoint=arguments["entrypoint"],
        inspect_models=arguments["inspect_models"],
        max_rows_per_table=arguments["max_rows_per_table"],
        evidence={
            "operation": "closed_input_extraction",
            "source_files": [
                {"name": name, "sha256": digest(raw), "bytes": len(raw)}
                for name, raw in members.items()
            ],
        },
    )


def parse_content(arguments):
    content = base64.b64decode(arguments['content_base64'], validate=True)
    if len(content) > arguments['max_file_bytes'] or len(content) > MAX_BYTES:
        fail('FILE_BYTE_BUDGET')
    if digest(content) != arguments['expected_sha256']:
        fail('SOURCE_CHANGED')
    return encoded(arguments['logical_name'], content, **arguments['parse_options'],
        evidence={'operation': 'proposed_export_extraction'})


def generate(arguments):
    if arguments["files"] is not None:
        from .powerbi_json_schema import validate_project_members
        from .powerbi_parsers import member_name

        members = {
            member_name(name): value.encode("utf-8") for name, value in arguments["files"].items()
        }
        if len(members) != len(arguments["files"]) or any(
            not allowed_companion(name) for name in members
        ):
            fail("GENERATION_MEMBER_INVALID")
        for name, value in arguments["resources_base64"].items():
            name = member_name(name)
            if name in members or PurePosixPath(name).suffix.lower() not in {
                ".png",
                ".jpg",
                ".jpeg",
                ".svg",
                ".pbiviz",
            }:
                fail("GENERATION_RESOURCE_INVALID")
            members[name] = base64.b64decode(value, validate=True)
        evidence = validate_project_members(members, arguments["entrypoint"])
        content = bundle(members)
        return encoded(
            arguments["logical_name"],
            content,
            entrypoint=arguments["entrypoint"],
            evidence={"operation": "generate_project_report", "validation": evidence},
        )
    from .powerbi_native import parse_native

    raw = canonical_json_bytes(arguments["database"])
    if len(raw) > 8_388_608:
        fail("MODEL_BYTE_BUDGET")
    native, evidence = parse_native("bim", [(arguments["logical_name"], raw)])
    content = canonical_json_bytes(native["database"])
    return encoded(
        arguments["logical_name"],
        content,
        evidence={"operation": "generate_bim", "native_generation": evidence},
    )


def edit(arguments):
    from .powerbi_authoring import edit_model

    raw, evidence = edit_model(arguments)
    options = {**arguments["parse_options"], "inspect_models": True}
    return encoded(arguments["logical_name"], raw, **options, evidence=evidence)


def powerbi_worker_operations():
    from .workers import WorkerOperation

    return (
        WorkerOperation('powerbi_parse_content', __name__, 'parse_content',
            max_input_bytes=33554432, max_output_bytes=50331648),
        WorkerOperation(
            "powerbi_parse_file",
            __name__,
            "parse_file",
            path_fields=("filenames",),
            max_output_bytes=50_331_648,
        ),
        WorkerOperation(
            "powerbi_generate",
            __name__,
            "generate",
            max_output_bytes=50_331_648,
            max_input_bytes=33_554_432,
        ),
        WorkerOperation(
            "powerbi_edit",
            __name__,
            "edit",
            max_output_bytes=50_331_648,
            max_input_bytes=33_554_432,
        ),
    )

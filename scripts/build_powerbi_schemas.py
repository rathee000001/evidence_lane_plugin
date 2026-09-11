"""Vendor exact Microsoft Power BI JSON schema bytes for offline validation."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = (
    ROOT / "plugins/evidence-lane-plugin/src/evidence_lane_plugin/contracts/powerbi-schemas"
)
COMMIT = "83ce11373faada0d01e76264a5cceb0ba70003e6"
PREFIX = "https://developer.microsoft.com/json-schemas/"
RAW = "https://raw.githubusercontent.com/microsoft/json-schemas/" + COMMIT + "/"


def get(url):
    request = Request(url, headers={"User-Agent": "EvidenceLaneSchemaQualification/4.0"})
    with urlopen(request, timeout=30) as response:
        content = response.read(8_388_609)
    if len(content) > 8_388_608:
        raise ValueError("Upstream schema resource exceeds its acquisition budget.")
    return content


def references(value):
    stack = [value]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("$ref"), str):
                yield node["$ref"]
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    tree = json.loads(
        get(
            "https://api.github.com/repos/microsoft/json-schemas/git/trees/"
            + COMMIT
            + "?recursive=1"
        )
    )
    if tree.get("truncated"):
        raise ValueError("The exact upstream source tree must be complete.")
    blobs = {row["path"]: row["sha"] for row in tree["tree"] if row["type"] == "blob"}
    pending = {
        path
        for path in blobs
        if path.endswith(".json")
        and path.startswith(
            (
                "fabric/item/report/definition/",
                "fabric/item/report/definitionProperties/",
                "fabric/item/semanticModel/definitionProperties/",
                "fabric/pbip/pbipProperties/",
            )
        )
    }
    files, records, loaded = {}, [], set()
    while pending:
        paths = sorted(pending - loaded)
        if not paths:
            break
        if len(loaded) + len(paths) > 256:
            raise ValueError("The admitted schema closure exceeds its bounded source count.")
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda path: get(RAW + path), paths))
        pending = set()
        for path, content in zip(paths, values, strict=True):
            blob = hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()
            if blob != blobs[path]:
                raise ValueError("Schema bytes differ from the pinned Git blob: " + path)
            document = json.loads(content)
            uri = PREFIX + path
            sha = hashlib.sha256(content).hexdigest()
            filename = sha + ".json"
            files[filename] = content
            records.append(
                {
                    "uri": uri,
                    "path": filename,
                    "sha256": sha,
                    "bytes": len(content),
                    "source_path": path,
                    "source_git_blob": blob,
                    "source_url": RAW + path,
                }
            )
            loaded.add(path)
            for ref in references(document):
                target, _ = urldefrag(urljoin(uri, ref))
                if target == uri:
                    continue
                if not target.startswith(PREFIX) or urlparse(target).query:
                    raise ValueError("Schema references an unadmitted external resource: " + target)
                relative = target.removeprefix(PREFIX)
                if relative not in blobs:
                    raise ValueError(
                        "Referenced schema is absent from the pinned source: " + relative
                    )
                if relative not in loaded:
                    pending.add(relative)
    license_bytes = get(RAW + "LICENSE")
    files["LICENSE.txt"] = license_bytes
    manifest = {
        "schema": "evidence-lane.powerbi-json-schemas.v4",
        "upstream_repository": "https://github.com/microsoft/json-schemas",
        "upstream_commit": COMMIT,
        "license_sha256": hashlib.sha256(license_bytes).hexdigest(),
        "resources": sorted(records, key=lambda row: row["uri"]),
        "runtime_network_resolution": False,
    }
    files["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    if args.check:
        if not DESTINATION.is_dir() or {path.name for path in DESTINATION.iterdir()} != set(files):
            raise ValueError("The packaged schema file set differs from the admitted closure.")
        for name, content in files.items():
            if (DESTINATION / name).read_bytes() != content:
                raise ValueError("Packaged schema bytes differ: " + name)
    else:
        DESTINATION.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (DESTINATION / name).write_bytes(content)
    print(
        json.dumps(
            {
                "resources": len(records),
                "files": len(files),
                "bytes": sum(map(len, files.values())),
                "commit": COMMIT,
                "mode": "check" if args.check else "write",
            }
        )
    )


if __name__ == "__main__":
    main()

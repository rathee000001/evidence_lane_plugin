"""Turn an actual pip resolution into exact-URL, hash-locked optional runtimes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--id", choices=("cpu", "cuda", "directml", "rocm"), required=True)
    parser.add_argument('--operation', choices=('code_embed_text', 'rapidocr_lines'), action='append', default=[])
    parser.add_argument('--base-manifest', type=Path)
    arguments = parser.parse_args()
    workspace = Path(__file__).resolve().parents[1]
    report = json.loads(arguments.report.read_text(encoding="utf-8"))
    rows = []
    allowed_hosts = {"download.pytorch.org", "download-r2.pytorch.org", "files.pythonhosted.org", "repo.radeon.com"}
    for item in report["install"]:
        url = item["download_info"]["url"]
        parsed = urlsplit(url)
        digest = item["download_info"]["archive_info"].get("hashes", {}).get("sha256")
        name = re.sub(r"[-_.]+", "-", item["metadata"]["name"]).lower()
        if parsed.scheme == "file" and name == "rocm" and arguments.id == "rocm":
            base = workspace / "contracts/optional-runtimes"
            receipt_path = base / "rocm-source-build.json"
            receipt_bytes = receipt_path.read_bytes()
            build = json.loads(receipt_bytes)
            body = dict(build)
            receipt_sha256 = body.pop("receipt_sha256", None)
            wheel = base / build["wheel_file"]
            if (wheel.resolve().parent != (base / "wheels").resolve()
                    or url != wheel.as_uri()
                    or digest != build["wheel_sha256"]
                    or hashlib.sha256(wheel.read_bytes()).hexdigest() != digest
                    or build.get("status") != "REPRODUCIBLE_LICENSE_RESTORED_WHEEL"
                    or receipt_sha256 != hashlib.sha256(canonical(body)).hexdigest()):
                raise ValueError("ROCm metadata wheel does not match its reviewed source-build receipt")
            rows.append({"name": name, "version": item["metadata"]["version"],
                         "wheel_file": build["wheel_file"], "sha256": digest,
                         "source_build_receipt": "rocm-source-build.json",
                         "source_build_receipt_sha256": hashlib.sha256(receipt_bytes).hexdigest()})
            continue
        if (parsed.scheme != "https" or parsed.hostname not in allowed_hosts or parsed.query
                or parsed.username or parsed.password or not parsed.path.endswith(".whl")
                or (digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest))):
            raise ValueError("Only hash-addressed wheels from the selected package publishers are admitted")
        if digest is None:
            directory = workspace / ".work/optional-wheels" / arguments.id
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / unquote(Path(parsed.path).name)
            if destination.parent != directory:
                raise ValueError("Wheel filename escaped its destination")
            if not destination.exists():
                print("Downloading exact wheel for hashing: " + name, flush=True)
                subprocess.run([sys.executable, "-I", "-m", "pip", "download", "--no-deps",
                                "--only-binary=:all:", "--dest", str(directory), url],
                               check=True, timeout=1800)
            if destination.stat().st_size > 6_442_450_944:
                raise ValueError("Optional wheel exceeded its six-GiB budget")
            with destination.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if name in {"torch", "torchvision"} and arguments.id in {"cpu", "cuda"}:
                old_lock = workspace / ".work/v3-base" / (
                    "requirements.torch-cpu.lock.txt" if arguments.id == "cpu" else "requirements.torch-nvidia.lock.txt")
                admitted = set(re.findall(r"sha256:([0-9a-f]{64})", old_lock.read_text()))
                if digest not in admitted:
                    raise ValueError("Downloaded runtime wheel does not match the captured implementation-base pins")
        rows.append({"name": name, "version": item["metadata"]["version"], "url": url, "sha256": digest})
    base_digest = None
    if arguments.base_manifest:
        original = json.loads(arguments.base_manifest.read_text())
        base_lock = arguments.base_manifest.parent / original['lock_file']
        if (original['runtime_id'] != arguments.id or original['python_version'] != report['environment']['python_version']
                or hashlib.sha256(base_lock.read_bytes()).hexdigest() != original['lock_sha256']):
            raise ValueError('The admitted base environment does not match this resolution target')
        names = {row['name'] for row in rows}
        rows.extend(row for row in original['packages'] if row['name'] not in names)
        base_digest = hashlib.sha256(arguments.base_manifest.read_bytes()).hexdigest()
    rows.sort(key=lambda row: row["name"])
    if len({row["name"] for row in rows}) != len(rows):
        raise ValueError("Package identities must be unique")
    operations = sorted(set(arguments.operation))
    for operation in operations:
        if operation == 'code_embed_text' and arguments.id not in {'cpu', 'cuda', 'rocm'}:
            raise ValueError('Embedding requires a separately pinned Torch environment')
        if operation == 'rapidocr_lines' and arguments.id != 'directml':
            raise ValueError('The isolated OCR provider contract requires DirectML')
    root = workspace / "contracts/optional-runtimes"
    root.mkdir(parents=True, exist_ok=True)
    lock = "# Exact resolved wheels; install only in the matching isolated runtime.\n" + "\n".join(
        (f"{row['name']} @ {row['url']}" if "url" in row else f"{row['name']}=={row['version']}")
        + f" --hash=sha256:{row['sha256']}" for row in rows) + "\n"
    lock_path = root / f"{arguments.id}.lock.txt"
    lock_path.write_text(lock, encoding="utf-8", newline="\n")
    manifest = {"schema_version": 1, "runtime_id": arguments.id,
                "python_version": report["environment"]["python_version"],
                "system": report["environment"]["platform_system"],
                "machine": report["environment"]["platform_machine"],
                "lock_file": lock_path.name, "lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
                "packages": rows, 'operations': operations,
                'resolution': {'report_sha256': hashlib.sha256(arguments.report.read_bytes()).hexdigest(),
                    'base_manifest_sha256': base_digest},
                "execution_qualification": "pending_runtime_probe_and_operation_qualification" if operations else "pending_runtime_probe"}
    (root / f"{arguments.id}.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"runtime": arguments.id, "python": manifest["python_version"], "packages": len(rows)}))


if __name__ == "__main__":
    main()

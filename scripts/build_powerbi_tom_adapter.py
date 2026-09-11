"""Build the locked Windows TOM adapter as an isolated candidate artifact."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "plugins/evidence-lane-plugin/toolchains/powerbi-adapter"
RUNTIME = "10.0.11"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / ".work/qualification/powerbi-tom-artifact"
    )
    parser.add_argument("--dotnet", type=Path, default=Path("C:/Program Files/dotnet/dotnet.exe"))
    args = parser.parse_args()
    output = args.output.resolve()
    if os.name != "nt" or not output.is_relative_to((ROOT / ".work").resolve()) or output.exists():
        raise ValueError("Select a new isolated artifact directory under .work on Windows.")
    sdk = subprocess.run(
        [str(args.dotnet), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if sdk != "10.0.102":
        raise ValueError("The current adapter build is pinned to .NET SDK 10.0.102.")
    cache = ROOT / ".work/qualification/powerbi-nuget"
    environment = {
        **os.environ,
        "NUGET_PACKAGES": str(cache),
        "DOTNET_CLI_HOME": str(ROOT / ".work/qualification/powerbi-dotnet-home"),
        "DOTNET_SKIP_FIRST_TIME_EXPERIENCE": "1",
        "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
        "DOTNET_GENERATE_ASPNET_CERTIFICATE": "false",
        "DOTNET_NOLOGO": "1",
    }
    subprocess.run(
        [
            str(args.dotnet),
            "publish",
            str(PROJECT / "EvidenceLane.PowerBi.csproj"),
            "-c",
            "Release",
            "--self-contained",
            "true",
            "-p:RestoreLockedMode=true",
            "-o",
            str(output),
            "-p:BaseIntermediateOutputPath="
            + str(ROOT / ".work/qualification/powerbi-tom-obj")
            + "/",
            "-p:BaseOutputPath=" + str(ROOT / ".work/qualification/powerbi-tom-bin") + "/",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    binary = output / "evidence-lane-powerbi.exe"
    reported = subprocess.run(
        [str(binary), "--version"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if reported != "evidence-lane-powerbi 4.0.0; Microsoft.AnalysisServices 19.114.12; protocol 1":
        raise ValueError("The built adapter reported a different protocol.")
    config = json.loads(
        (output / "evidence-lane-powerbi.runtimeconfig.json").read_text(encoding="utf-8")
    )
    frameworks = config["runtimeOptions"]["includedFrameworks"]
    if frameworks != [{"name": "Microsoft.NETCore.App", "version": RUNTIME}]:
        raise ValueError("The published adapter does not include the pinned .NET runtime.")
    lock = json.loads((PROJECT / "packages.lock.json").read_text(encoding="utf-8"))
    packages = {}
    for framework in lock["dependencies"].values():
        for name, row in framework.items():
            if row["type"] not in {"Direct", "Transitive"}:
                continue
            key = (name.lower(), row["resolved"])
            prior = packages.get(key)
            if prior and prior["contentHash"] != row["contentHash"]:
                raise ValueError("Conflicting locked package hashes.")
            packages[key] = row
    sources = output / "sources"
    (sources / "nuget").mkdir(parents=True)
    records = []
    for (name, version), row in sorted(packages.items()):
        package = cache / name / version / (name + "." + version + ".nupkg")
        content = package.read_bytes()
        archive_sha512 = base64.b64encode(hashlib.sha512(content).digest()).decode()
        if archive_sha512 != package.with_suffix(".nupkg.sha512").read_text().strip():
            raise ValueError("A restored NuGet archive differs from its lock: " + name)
        # NuGet's lock hash excludes signing metadata, unlike the archive hash.
        # Compute it with the same native SDK API used by locked restore.
        content_hash = subprocess.run(
            [
                "pwsh",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(ROOT / "scripts/read_nuget_content_hash.ps1"),
                "-SdkRoot",
                str(args.dotnet.parent / "sdk" / sdk),
                "-PackagePath",
                str(package),
            ],
            capture_output=True,
            text=True,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()
        if content_hash != row["contentHash"]:
            raise ValueError("NuGet package content differs from its exact lock: " + name)
        with zipfile.ZipFile(package) as zipped:
            nuspec = ET.fromstring(
                zipped.read(next(n for n in zipped.namelist() if n.endswith(".nuspec")))
            )
            metadata = next(n for n in nuspec if n.tag.endswith("metadata"))
            tags = {n.tag.rsplit("}", 1)[-1]: n.text for n in metadata}
        shutil.copyfile(package, sources / "nuget" / package.name)
        records.append(
            {
                "name": name,
                "version": version,
                "sha256": digest(package),
                "nuget_content_sha512": content_hash,
                "archive_sha512": archive_sha512,
                "license": tags.get("license"),
                "license_url": tags.get("licenseUrl"),
                "copyright": tags.get("copyright"),
                "source_url": "https://api.nuget.org/v3-flatcontainer/"
                + name
                + "/"
                + version
                + "/"
                + package.name,
            }
        )
    for path in (
        PROJECT / "EvidenceLane.PowerBi.csproj",
        PROJECT / "Program.cs",
        PROJECT / "packages.lock.json",
    ):
        shutil.copyfile(path, sources / path.name)
    for name in ("LICENSE.txt", "THIRD-PARTY-NOTICES.txt"):
        candidate = cache / "microsoft.netcore.app.runtime.win-x64" / RUNTIME / name
        if candidate.exists():
            shutil.copyfile(candidate, output / ("dotnet-" + name))
    receipt = {
        "schema": "evidence-lane.powerbi-tom-build.v4",
        "sdk": sdk,
        "runtime": RUNTIME,
        "package_version": "19.114.12",
        "reported_version": reported,
        "binary_sha256": digest(binary),
        "nuget_lock_sha256": digest(PROJECT / "packages.lock.json"),
        "packages": records,
        "adapter_sources": {
            path.name: digest(path)
            for path in (PROJECT / "Program.cs", PROJECT / "EvidenceLane.PowerBi.csproj")
        },
        "runtime_compilation": False,
        "user_installation_performed": False,
    }
    (output / "build-receipt.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "artifact": str(output),
                "runtime": RUNTIME,
                "packages": len(records),
                "binary_sha256": digest(binary),
            }
        )
    )


if __name__ == "__main__":
    main()

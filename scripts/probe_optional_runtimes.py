"""Measure optional development runtimes without changing any device setting."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins/evidence-lane-plugin/src"))

from evidence_lane_plugin.accelerators import probe_nvidia_devices
from evidence_lane_plugin.optional_runtimes import OptionalRuntime
from evidence_lane_plugin.provider_probe import enumerate_dxgi


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", choices=("cpu", "cuda", "rocm", "directml"))
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    devices = probe_nvidia_devices()
    dxgi = enumerate_dxgi()
    results = []
    directories = sorted((root / ".work/optional-envs").iterdir())
    if arguments.runtime:
        manifest = json.loads(
            (
                root
                / "contracts/optional-runtimes"
                / f"{arguments.runtime}.json"
            ).read_text(encoding="utf-8")
        )
        directories = [
            root
            / ".work/optional-envs"
            / f"{arguments.runtime}-{manifest['lock_sha256'][:16]}"
        ]
    for directory in directories:
        if not (directory / "environment.json").is_file():
            continue
        runtime = OptionalRuntime.from_installation(
            directory,
            root / "plugins/evidence-lane-plugin/toolchains/providers",
        )
        if arguments.runtime and runtime.runtime_id != arguments.runtime:
            continue
        if runtime.runtime_id == "cuda":
            selected = [{"device_id": item.device_id, "device_index": item.device_index} for item in devices]
        elif runtime.runtime_id == "directml":
            selected = [
                {"device_id": item.device_id, "device_index": item.device_index}
                for item in devices
            ] + [
                {"device_id": item["device_id"], "device_index": item["device_index"]}
                for item in dxgi
                if item["vendor_id"] == 0x1002
            ]
            if not selected:
                selected = [{"device_id": "unavailable", "device_index": 0}]
        else:
            selected = [{}]
        for selection in selected:
            result = runtime.probe(**selection, timeout=300)
            results.append(result)
            print(json.dumps(result), flush=True)
    destination = root / ".work/verification/optional-runtime-verification.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if arguments.runtime and destination.is_file():
        previous = json.loads(destination.read_text())
        results = [item for item in previous["probes"] if item["runtime_id"] != arguments.runtime] + results
    record = {"device_observations": [item.model_dump(mode="json") for item in devices],
              "dxgi_adapters": dxgi, "probes": results,
              "scope": "Small provider self-tests; not full profile workload qualification or installed-plugin proof."}
    destination.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

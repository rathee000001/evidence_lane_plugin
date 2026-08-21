"""Seal one exact GitHub App/SDK checkpoint-delivery receipt.

This public executable validates non-secret identities only.  It performs no
Git, network, package installation, marketplace mutation, lifecycle, HIL, or
PV action.  The produced receipt is immutable and may be consumed by the
governed checkpoint installer after the external actions have already passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PLUGIN_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from evidence_lane_plugin.github_app_distribution import (
    GitHubAppManifest,
    GitHubAppProductionDeliveryRoute,
    InstallationBinding,
    ProductionDeliveryIdentity,
    receipt_contains_secret,
)
from evidence_lane_plugin.hashing import (
    canonical_json_bytes,
    sha256_bytes,
)

REQUEST_SCHEMA = "evidence-lane.github-app-production-delivery-request.v1"


class ProductionDeliverySealError(RuntimeError):
    """Raised before output when the public delivery request is not exact."""


def _exact_fields(value: object, expected: set[str], *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ProductionDeliverySealError(
            f"{field} must contain exactly: {', '.join(sorted(expected))}"
        )
    return dict(value)


def seal_request(request: dict[str, Any]) -> dict[str, Any]:
    top = _exact_fields(
        request,
        {"schema", "manifest", "installation_binding", "delivery"},
        field="request",
    )
    if top["schema"] != REQUEST_SCHEMA:
        raise ProductionDeliverySealError("Unsupported production-delivery request schema.")
    manifest = GitHubAppManifest.from_mapping(
        _exact_fields(
            top["manifest"],
            {
                "schema",
                "app_slug",
                "manifest_version",
                "repository_selection",
                "repository_permissions",
                "events",
                "public",
            },
            field="manifest",
        )
    )
    binding_fields = _exact_fields(
        top["installation_binding"],
        {
            "binding_id",
            "installation_id",
            "project_id",
            "task_id",
            "accepted_pv",
            "repositories",
            "permissions",
            "expires_at",
        },
        field="installation_binding",
    )
    binding = InstallationBinding.create(manifest=manifest, **binding_fields)
    delivery_fields = _exact_fields(
        top["delivery"],
        {
            "delivery_id",
            "repository",
            "branch",
            "commit_sha",
            "tree_sha",
            "actions_run_id",
            "actions_status",
            "actions_conclusion",
            "actions_head_sha",
            "package_id",
            "package_version",
            "package_sha256",
            "package_source_commit",
            "mutable_local_slot",
            "branch_commit_slot",
            "main_merge_fallback_slot",
            "installed_version",
            "installed_package_sha256",
            "installed_surface_sha256",
            "main_merge_fallback_before_sha256",
            "main_merge_fallback_after_sha256",
        },
        field="delivery",
    )
    identity = ProductionDeliveryIdentity.create(
        installation_binding=binding, **delivery_fields
    )
    receipt = GitHubAppProductionDeliveryRoute().seal(identity)
    if receipt_contains_secret(receipt):
        raise ProductionDeliverySealError("The delivery receipt contains a secret field.")
    return receipt


def seal_file(*, request_path: Path, output_path: Path) -> dict[str, Any]:
    request = json.loads(request_path.resolve().read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ProductionDeliverySealError("The delivery request must be one JSON object.")
    receipt = seal_request(request)
    content = canonical_json_bytes(receipt)
    output = output_path.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_bytes() != content:
        raise ProductionDeliverySealError(
            "Refusing to overwrite another production-delivery receipt."
        )
    if not output.exists():
        output.write_bytes(content)
    return {
        "status": "PASS",
        "receipt": str(output),
        "receipt_sha256": receipt["receipt_sha256"],
        "file_sha256": sha256_bytes(content),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    print(json.dumps(seal_file(request_path=args.request, output_path=args.output)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Bind SDK inspection to the current authority package owners."""

from evidence_lane_plugin.authority_support import (
    authority_actions,
    authority_package_contract,
    authority_package_folder,
)

from ..contracts import load_sdk_contract


def authority_sdk_catalog():
    """Return retained authority, sector, workflow-owner and Root-PV bindings."""

    return {
        name: load_sdk_contract(path)
        for name, path in {
            "authorities": "authorities/named-authorities.ref.v4.json",
            "sectors": "authorities/project-sectors.ref.v4.json",
            "workflow_owners": "authorities/workflow-owners.ref.v4.json",
            "env_uop": "authorities/env-uop.ref.v4.json",
            "root_pv": "authorities/root-pv.ref.v4.json",
            "removed": "authorities/removed-authorities.v4.json",
        }.items()
    }

__all__ = [
    "authority_actions",
    "authority_package_contract",
    "authority_package_folder",
    "authority_sdk_catalog",
]

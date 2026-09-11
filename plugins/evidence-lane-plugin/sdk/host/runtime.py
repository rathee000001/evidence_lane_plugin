"""Bind SDK callers to measured host observation and route selection."""

from evidence_lane_plugin.host_routing import (
    ClientHello,
    HostDetector,
    HostObservation,
    select_host_route,
)

from ..contracts import load_sdk_contract


def host_contract_catalog():
    """Return measured-host, installation, startup, Plan and Studio contracts."""

    return load_sdk_contract("host/host-contract-registry.v4.json")

__all__ = [
    "ClientHello",
    "HostDetector",
    "HostObservation",
    "host_contract_catalog",
    "select_host_route",
]

"""Bind the outer SDK to authenticated local and explicit remote transports."""

from evidence_lane_plugin.local_transport import LocalTransport
from evidence_lane_plugin.remote_transport import RemoteClientConfig, RemoteTransport

from ..contracts import load_sdk_contract


def transport_contract_catalog():
    """Return local API, MCP stdio, remote API and hook-capture contracts."""

    return load_sdk_contract("transports/transport-registry.v4.json")

__all__ = [
    "LocalTransport",
    "RemoteClientConfig",
    "RemoteTransport",
    "transport_contract_catalog",
]

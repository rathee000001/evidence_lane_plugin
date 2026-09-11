"""Bind the outer SDK to authenticated local and explicit remote transports."""

from evidence_lane_plugin.local_transport import LocalTransport
from evidence_lane_plugin.remote_transport import RemoteClientConfig, RemoteTransport

__all__ = ["LocalTransport", "RemoteClientConfig", "RemoteTransport"]

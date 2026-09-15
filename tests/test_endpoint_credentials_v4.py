"""The local endpoint credential owner is Windows-only."""

import pytest
from evidence_lane_plugin import endpoint_credentials
from evidence_lane_plugin.errors import LaneError


@pytest.mark.parametrize("host_os", ["posix", "java"])
def test_non_windows_endpoint_credential_route_is_rejected(host_os, monkeypatch, tmp_path):
    monkeypatch.setattr(endpoint_credentials.os, "name", host_os)
    with pytest.raises(LaneError) as error:
        endpoint_credentials.write_private_record(
            tmp_path / "endpoint.json",
            {"protocol_version": 4},
            "x" * 48,
        )
    assert error.value.code == "WINDOWS_HOST_REQUIRED"
    assert not (tmp_path / "endpoint.json").exists()

"""OS-owner mode checks for the reduced POSIX route, not an actual Mac run."""
import stat
from types import SimpleNamespace

import pytest
from evidence_lane_plugin import endpoint_credentials
from evidence_lane_plugin.errors import LaneError


@pytest.mark.parametrize('mode,uid,links,directory,accepted', [
    (stat.S_IFREG | 0o600, 42, 1, False, True),
    (stat.S_IFREG | 0o640, 42, 1, False, False),
    (stat.S_IFREG | 0o600, 43, 1, False, False),
    (stat.S_IFREG | 0o600, 42, 2, False, False),
    (stat.S_IFLNK | 0o600, 42, 1, False, False),
    (stat.S_IFDIR | 0o700, 42, 2, True, True),
    (stat.S_IFDIR | 0o755, 42, 2, True, True),
    (stat.S_IFDIR | 0o775, 42, 2, True, False),
])
def test_posix_permission_gate(mode, uid, links, directory, accepted, monkeypatch, tmp_path):
    monkeypatch.setattr(endpoint_credentials.os, 'getuid', lambda: 42, raising=False)
    info = SimpleNamespace(st_mode=mode, st_uid=uid, st_nlink=links)
    if accepted:
        endpoint_credentials._posix_owner(tmp_path, info, directory=directory)
    else:
        with pytest.raises(LaneError) as error:
            endpoint_credentials._posix_owner(tmp_path, info, directory=directory)
        assert error.value.code == 'ENDPOINT_OWNER_PERMISSIONS'

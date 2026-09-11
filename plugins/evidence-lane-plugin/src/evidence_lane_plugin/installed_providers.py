"""Read shared provider installations using the current packaged contracts."""
from __future__ import annotations

import hashlib
from pathlib import Path

from .errors import LaneError
from .installation_layout import studio_installation
from .optional_runtimes import OptionalRuntime, read_bounded_json
from .storage import reject_links


def load_installed_providers(*, installation=None, contracts=None):
    installation = installation or studio_installation()
    contracts = contracts or Path(__file__).resolve().parents[2] / 'toolchains/providers'
    path = installation.toolchains / 'provider-installation.v4.json'
    state = {'state': 'not_installed', 'providers': [], 'full_bundle_ready': False,
        'execution_state': 'not_probed_by_installation_read'}
    if not path.exists():
        return (), state
    try:
        reject_links(path, installation.root)
        record = read_bounded_json(path)
        if (record['schema'] != 'evidence-lane.provider-installation.v4'
                or Path(record['installation_root']).resolve() != installation.root.resolve()
                or not isinstance(record['providers'], list) or not 1 <= len(record['providers']) <= 4
                or len({row['runtime_id'] for row in record['providers']}) != len(record['providers'])):
            raise ValueError('Provider installation index changed')
        runtimes, statuses = [], []
        for row in record['providers']:
            identity = row['runtime_id']
            if identity not in {'cpu', 'cuda', 'rocm', 'directml'}:
                raise ValueError('Provider identifier changed')
            try:
                manifest = contracts / (identity + '.json')
                digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
                contract = read_bounded_json(manifest)
                directory = installation.python_environments / 'providers' / (identity + '-' + contract['lock_sha256'][:16])
                if (row['environment'] != directory.relative_to(installation.root).as_posix()
                        or row['manifest_sha256'] != digest or row['lock_sha256'] != contract['lock_sha256']):
                    raise ValueError('Provider index has a stale contract or foreign environment')
                reject_links(directory, installation.root)
                runtime = OptionalRuntime.from_installation(directory, contracts)
                runtimes.append(runtime)
                statuses.append({'runtime_id': identity, 'state': 'installed_record_verified',
                    'operations': contract.get('operations', []), 'environment_digest': runtime.lock_sha256,
                    'execution_state': 'not_probed_by_installation_read'})
            except (LaneError, OSError, ValueError, KeyError, TypeError):
                statuses.append({'runtime_id': identity, 'state': 'unavailable', 'reason': 'PROVIDER_INSTALLATION_BINDING_INVALID'})
        return tuple(runtimes), state | {'state': 'observed', 'providers': statuses}
    except (LaneError, OSError, ValueError, KeyError, TypeError):
        return (), state | {'state': 'unavailable', 'reason': 'PROVIDER_INSTALLATION_INDEX_INVALID'}

"""One Windows Studio installation shared by every selected project and client."""
from __future__ import annotations

import hashlib
import json
import os
import platform
from dataclasses import dataclass
from pathlib import Path

from .errors import LaneError
from .storage import reject_links


@dataclass(frozen=True)
class StudioInstallation:
    root: Path
    selected_release: Path | None = None

    @property
    def active_root(self) -> Path:
        if self.selected_release is not None:
            release = self.selected_release.resolve(strict=True)
            if release.parent != self.root.resolve() / "releases":
                raise LaneError(
                    "STUDIO_INSTALLATION_INVALID",
                    "The selected release is outside the shared installation.",
                )
            reject_links(release, self.root)
            return release
        pointer = self.root / "installation.json"
        if not pointer.is_file():
            return self.root
        try:
            value = json.loads(pointer.read_text(encoding="utf-8"))
            body = dict(value)
            receipt = body.pop("receipt_sha256")
            encoded = json.dumps(
                body,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            relative = Path(value["active_release"])
            release = (self.root / relative).resolve()
            if (
                value.get("schema") != "evidence-lane.first-detection-installation.v4"
                or value.get("status") != "ACTIVE_RELEASE"
                or receipt != hashlib.sha256(encoded).hexdigest()
                or relative.is_absolute()
                or ".." in relative.parts
                or release.parent != self.root.resolve() / "releases"
                or not release.is_dir()
            ):
                raise ValueError()
            reject_links(release, self.root)
            return release
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise LaneError(
                "STUDIO_INSTALLATION_INVALID",
                "The active shared Studio installation pointer is invalid.",
            ) from None

    @property
    def app(self) -> Path:
        return self.active_root / 'app'

    @property
    def engine_runtime(self) -> Path:
        return self.active_root / 'runtime/engine'

    @property
    def toolchains(self) -> Path:
        return self.active_root / 'toolchains'

    @property
    def native_tools(self) -> Path:
        return self.toolchains / 'bin'

    @property
    def models(self) -> Path:
        return self.toolchains / 'models'

    @property
    def python_environments(self) -> Path:
        return self.toolchains / 'python'

    @property
    def node_environment(self) -> Path:
        return self.toolchains / 'node'

    @property
    def installation_receipt(self) -> Path:
        return self.root / 'installation.json'


def studio_installation(*, environment=None, system=None) -> StudioInstallation:
    environment = os.environ if environment is None else environment
    system = platform.system() if system is None else system
    if system != 'Windows':
        raise LaneError('STUDIO_PLATFORM_UNSUPPORTED',
                        'Studio and its managed toolchain install on Windows PCs only.')
    value = environment.get('EVIDENCE_LANE_STUDIO_ROOT', 'C:/Apps/EvidenceLaneStudio')
    if not isinstance(value, str) or not value.strip() or any(c in value for c in '\r\n\x00'):
        raise LaneError('STUDIO_INSTALL_ROOT_INVALID', 'Select an absolute Studio installation directory.')
    root = Path(value).expanduser()
    if not root.is_absolute() or '..' in root.parts:
        raise LaneError('STUDIO_INSTALL_ROOT_INVALID', 'Select an absolute Studio installation directory.')
    root = Path(os.path.abspath(root))
    reject_links(root, Path(root.anchor))
    return StudioInstallation(root)

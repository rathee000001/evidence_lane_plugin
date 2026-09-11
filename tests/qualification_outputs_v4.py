"""Per-run visual evidence paths; prior qualification artifacts are immutable."""
import os
from pathlib import Path


def qualification_output(relative, fallback):
    root = Path(os.environ.get('EVI_QUALIFICATION_OUTPUT', str(fallback))).resolve()
    path = (root / relative).resolve()
    assert path.is_relative_to(root) and path != root
    assert not path.exists(), f'Preserve existing qualification output: {path}'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

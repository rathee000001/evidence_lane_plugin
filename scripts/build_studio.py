"""Build Studio and publish only declared static assets into the Python package."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'apps/evidence-lane-studio'
OUT = ROOT / '.work/studio-dist'
DESTINATION = ROOT / 'plugins/evidence-lane-plugin/src/evidence_lane_plugin/studio'


def main():
    # Vite's emptyOutDir can remove prior build output. Resolve and verify that
    # exact directory before invoking it, and refuse symlink/junction parents.
    if OUT.resolve() != OUT or not OUT.resolve().is_relative_to((ROOT / '.work').resolve()):
        raise SystemExit('Studio output must stay in this workspace .work directory')
    for path in [OUT, *OUT.parents]:
        if path.exists() and (path.is_symlink() or path.is_junction()):
            raise SystemExit('A Studio build path cannot be a link')
    npm = shutil.which('npm.cmd' if __import__('os').name == 'nt' else 'npm')
    if not npm:
        raise SystemExit('Node/npm is required for the Studio build')
    result = subprocess.run([npm, 'run', 'build'], cwd=APP, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
    entries = {}
    allowed = {'.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
               '.css': 'text/css; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png'}
    files = sorted(path for path in OUT.rglob('*') if path.is_file())
    if len(files) > 100 or sum(path.stat().st_size for path in files) > 32_000_000:
        raise SystemExit('Studio build exceeds its static asset budget')
    for source in files:
        relative = source.relative_to(OUT)
        if source.suffix not in allowed or source.is_symlink() or source.stat().st_size > 8_000_000:
            raise SystemExit('Unexpected Studio build asset')
        target = DESTINATION / relative
        if not target.resolve().is_relative_to(DESTINATION.resolve()):
            raise SystemExit('Studio assets must stay in the package static directory')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        route = '/studio/' if relative.as_posix() == 'index.html' else '/studio/' + relative.as_posix()
        entries[route] = {'file': relative.as_posix(), 'content_type': allowed[source.suffix],
                          'sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'bytes': source.stat().st_size}
    assert {'/studio/', '/studio/app.js', '/studio/styles.css'} <= set(entries)
    (DESTINATION/'assets-manifest.json').write_text(json.dumps({'schema_version': 1, 'assets': entries}, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'assets': len(entries), 'bytes': sum(entry['bytes'] for entry in entries.values()), 'destination': str(DESTINATION)}))


if __name__ == '__main__':
    main()

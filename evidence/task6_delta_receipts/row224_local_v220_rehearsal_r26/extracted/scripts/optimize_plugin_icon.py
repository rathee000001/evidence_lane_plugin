"""Derive the compact plugin PNG from the authorized Evidence Lane cube asset.

This is a mechanical resize and palette optimization only.  It creates no new
artwork and keeps the alpha channel so compact Codex surfaces show the supplied
cube without a telemetry/root-node substitution.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import tempfile
from pathlib import Path

from PIL import Image


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, action="append", required=True)
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--max-bytes", type=int, default=10 * 1024)
    return parser


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def main() -> int:
    args = _parser().parse_args()
    source = args.source.resolve()
    source_bytes = source.read_bytes()
    with Image.open(io.BytesIO(source_bytes)) as image:
        compact = image.convert("RGBA").resize(
            (args.size, args.size),
            Image.Resampling.LANCZOS,
        )
        indexed = compact.quantize(
            colors=256,
            method=Image.Quantize.FASTOCTREE,
            dither=Image.Dither.NONE,
        )
        output = io.BytesIO()
        indexed.save(output, format="PNG", optimize=True, compress_level=9)
    payload = output.getvalue()
    if len(payload) > args.max_bytes:
        raise SystemExit(
            f"Optimized icon is {len(payload)} bytes; limit is {args.max_bytes}."
        )
    for target in args.output:
        _write_atomic(target.resolve(), payload)
    print(
        json.dumps(
            {
                "status": "PASS",
                "source": str(source),
                "source_sha256": _sha256(source_bytes),
                "width": args.size,
                "height": args.size,
                "bytes": len(payload),
                "sha256": _sha256(payload),
                "outputs": [str(path.resolve()) for path in args.output],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

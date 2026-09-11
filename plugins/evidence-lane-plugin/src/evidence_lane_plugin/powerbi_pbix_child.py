"""Standalone Python 3.13 PBIX reader. Closed stdin/stdout; no plugin imports."""

from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import io
import json
import math
import platform
import sys
import zipfile
from datetime import date, datetime, timedelta
from decimal import Decimal

MAX_INPUT = 25_165_824
MAX_OUTPUT = 16_777_216
MAX_ROWS = 25_000
MAX_CELLS = 100_000


def scalar(value):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, (datetime, date)):
        return {
            "type": "datetime" if isinstance(value, datetime) else "date",
            "value": value.isoformat(),
        }
    if isinstance(value, timedelta):
        return {"type": "timedelta", "value": str(value)}
    if isinstance(value, bytes):
        return {"type": "bytes", "base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, float):
        return (
            value if math.isfinite(value) else {"type": "nonfinite_or_missing", "value": str(value)}
        )
    if hasattr(value, "item"):
        return scalar(value.item())
    import pandas as pd

    if pd.isna(value):
        return None
    raise ValueError("Unsupported decoded cell type.")


def records(frame):
    if len(frame) > MAX_ROWS or len(frame.columns) > 4096:
        raise ValueError("Metadata budget exceeded.")
    columns = [str(column) for column in frame.columns]
    return [
        {name: scalar(value) for name, value in zip(columns, row, strict=True)}
        for row in frame.itertuples(index=False, name=None)
    ]


def inspect(request):
    if set(request) != {"schema", "content_base64", "sha256", "max_rows_per_table"}:
        raise ValueError("Unexpected protocol field.")
    if request["schema"] != "evidence-lane.powerbi-pbix-request.v1":
        raise ValueError("Invalid protocol.")
    raw = base64.b64decode(request["content_base64"], validate=True)
    if len(raw) > 16_777_216 or hashlib.sha256(raw).hexdigest() != request["sha256"]:
        raise ValueError("Input identity mismatch.")
    limit = request["max_rows_per_table"]
    if type(limit) is not int or not 0 <= limit <= 1000:
        raise ValueError("Invalid row budget.")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.infolist()
        if len(info) > 256 or sum(row.file_size for row in info) > 16_777_216:
            raise ValueError("Invalid package budget.")
        if "DataModel" not in archive.namelist():
            raise ValueError("No embedded DataModel.")
    from pbixray import PBIXRay

    # File-like input prevents the reader from mapping or following a host path.
    # In-memory decompression stays within the parent's hard process-memory cap.
    with PBIXRay(io.BytesIO(raw), on_disk=False) as model:
        names = [str(name) for name in model.tables]
        if len(names) > 128 or len(set(names)) != len(names):
            raise ValueError("Model table budget exceeded.")
        properties = [
            "schema",
            "relationships",
            "rls",
            "ols",
            "power_query",
            "m_parameters",
            "dax_tables",
            "dax_measures",
            "dax_columns",
            "statistics",
            "metadata",
            "aggregations",
        ]
        properties.extend(
            sorted(
                name
                for name, member in vars(PBIXRay).items()
                if name.startswith("tmschema_") and isinstance(member, property)
            )
        )
        collections, total = {}, 0
        for name in properties:
            rows = records(getattr(model, name))
            total += len(rows)
            if total > MAX_ROWS:
                raise ValueError("Total metadata budget exceeded.")
            collections[name] = rows
        tables, cells = [], 0
        for name in names:
            sample, columns, has_more = [], [], False
            if limit:
                iterator = model.iter_table(name, chunk_size=limit + 1, strings_as_categorical=True)
                try:
                    for frame in iterator:
                        columns = [str(column) for column in frame.columns]
                        if len(columns) > 4096:
                            raise ValueError("Table width budget exceeded.")
                        for row in frame.itertuples(index=False, name=None):
                            if len(sample) == limit:
                                has_more = True
                                break
                            cells += len(row)
                            if cells > MAX_CELLS:
                                raise ValueError("Cell sample budget exceeded.")
                            sample.append([scalar(value) for value in row])
                        if has_more:
                            break
                finally:
                    iterator.close()
            tables.append(
                {
                    "name": name,
                    "columns": columns,
                    "rows": sample,
                    "has_more_rows": has_more,
                    "rows_requested": bool(limit),
                    "sample_limit": limit,
                }
            )
        return {
            "schema": "evidence-lane.powerbi-pbix.v1",
            "sha256": request["sha256"],
            "package_version": importlib.metadata.version("pbixray"),
            "python_version": platform.python_version(),
            "tables": tables,
            "metadata_collections": collections,
            "connections": model.connections,
            "metadata_read": True,
            "rows_requested": bool(limit),
            "rows_decoded": any(table["rows"] for table in tables),
            "bounded_row_samples": True,
            "server_connected": False,
            "dax_executed": False,
            "data_refreshed": False,
            "source_bytes_mutated": False,
            "network_used": False,
        }


def main():
    if sys.argv[1:] == ["--version"]:
        print(
            "evidence-lane-pbix 4.0.0; PBIXRay "
            + importlib.metadata.version("pbixray")
            + "; Python "
            + platform.python_version()
            + "; protocol 1"
        )
        return 0
    if sys.argv[1:]:
        return 1
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("Protocol input budget exceeded.")
        result = inspect(json.loads(raw))
        output = json.dumps(
            result, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode("utf-8")
        if len(output) > MAX_OUTPUT:
            raise ValueError("Protocol output budget exceeded.")
        sys.stdout.buffer.write(output)
        return 0
    except Exception:  # noqa: BLE001 - vendor errors must not echo model data or connection values.
        sys.stderr.write("POWERBI_PBIX_INPUT_REJECTED")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

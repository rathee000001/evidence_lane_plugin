"""Separate MMD, DOT and pointer views of media versions and derived evidence."""

from .lane_contract import LaneView, ViewGraph
from .media_parsers import KINDS
from .media_schema import MEDIA_MIGRATIONS


def media_heads(store):
    from .media_profile import current_media

    current = current_media(store)
    if not current["initialized"]:
        return {"images_ocr": current}
    with store.lane("images_ocr").connection(read_only=True) as connection:
        derived = {
            kind: [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY created_at DESC LIMIT 129"
                ).fetchall()
            ]
            for kind, table in [("ocr", "media_ocr_run"), ("extraction", "media_extraction")]
        }
    return {"images_ocr": current, "derivatives": derived}


def media_graph(store, scope):
    from .media_derivatives import manifest as derived_manifest
    from .media_derivatives import ocr_rows
    from .media_profile import current_media, read_snapshot

    graph = ViewGraph(store.project_id, scope)
    current = current_media(store)
    for row in current["media"]:
        if scope.query and scope.query != row["media_id"]:
            continue
        manifest, facts = read_snapshot(store, row["snapshot_id"])
        root = graph.node(
            "media",
            row["snapshot_id"],
            manifest["logical_name"],
            locator={
                "snapshot_id": row["snapshot_id"],
                "media_id": row["media_id"],
                "sha256": manifest["raw_object"],
                "source_object": manifest["source_object"],
            },
        )
        frames = {}
        for item in facts["items"]:
            node = graph.node(
                "media_" + item["kind"],
                row["snapshot_id"] + ":" + item["item_id"],
                str(item.get("name") or item["text"] or item["kind"])[:100],
                locator={
                    "snapshot_id": row["snapshot_id"],
                    "item_id": item["item_id"],
                    "frame": item.get("frame"),
                    "part": item["part"],
                    "kind": item["kind"],
                },
            )
            graph.edge(root, node, "CONTAINS")
            if item["kind"] == "frame":
                frames[item["frame"]] = node
            if graph.truncated:
                break
        if graph.truncated:
            break
        with store.lane("images_ocr").connection(read_only=True) as connection:
            runs = connection.execute(
                "SELECT ocr_id FROM media_ocr_run WHERE snapshot_id=? ORDER BY created_at DESC LIMIT 33",
                (row["snapshot_id"],),
            ).fetchall()
            files = connection.execute(
                "SELECT extraction_id FROM media_extraction WHERE snapshot_id=? ORDER BY created_at DESC LIMIT 33",
                (row["snapshot_id"],),
            ).fetchall()
        for run in runs[:32]:
            body, lines = ocr_rows(store, run["ocr_id"])
            node = graph.node(
                "media_ocr_run",
                run["ocr_id"],
                "Separate frame OCR",
                locator={"snapshot_id": row["snapshot_id"], "ocr_id": run["ocr_id"]},
            )
            graph.edge(node, root, "DERIVED_FROM")
            for frame in body['result'].get('review_frames', []):
                child = graph.node('media_review_frame', run['ocr_id'] + ':frame:' + str(frame['frame']),
                    'Empty OCR frame requires review', locator={'ocr_id': run['ocr_id'], **frame})
                graph.edge(node, child, 'REQUIRES_REVIEW')
                if frame['frame'] in frames:
                    graph.edge(frames[frame['frame']], child, 'OCR_EVIDENCE')
                if graph.truncated:
                    break
            for line in lines:
                child = graph.node(
                    "media_ocr_line",
                    run["ocr_id"] + ":" + line["line_id"],
                    line["text"][:100],
                    locator={
                        "ocr_id": run["ocr_id"],
                        "line_id": line["line_id"],
                        "frame": line["frame"],
                        "review_required": line["review_required"],
                    },
                )
                graph.edge(node, child, "CONTAINS")
                if line["frame"] in frames:
                    graph.edge(frames[line["frame"]], child, "OCR_EVIDENCE")
                if graph.truncated:
                    break
            if graph.truncated:
                break
        for file in files[:32]:
            if graph.truncated:
                break
            body = derived_manifest(store, file["extraction_id"], "extraction")
            node = graph.node(
                "media_extraction",
                file["extraction_id"],
                body["result"]["filename"],
                locator={
                    "snapshot_id": row["snapshot_id"],
                    "extraction_id": file["extraction_id"],
                    "sha256": body["result"]["sha256"],
                    "kind": body["request"]["kind"],
                    "requested_start_seconds": body["request"]["start_seconds"],
                },
            )
            graph.edge(node, root, "DERIVED_FROM")
        graph.truncated |= len(runs) > 32 or len(files) > 32
        if graph.truncated:
            break
    graph.truncated |= current.get("truncated", False)
    return graph.result()


def media_pointer(binding, graph, files):
    return {
        "schema": "evidence-lane.media-locators.v4",
        "snapshot_binding": binding,
        "locators": {
            node["key"]: {"node_id": node["id"], **node["locator"]} for node in graph["nodes"]
        },
        "artifacts": files,
        "native_and_ocr_evidence_separate": True,
        "ocr_accuracy_verified": False,
        "source_frame_pts_verified": False,
        "visual_review": "separate_operation",
    }


def register_media_views(engine):
    engine.registry.register_view(
        LaneView(
            "images_ocr.structure",
            "images_ocr",
            "sectors/images_ocr",
            "Image frames, passive vector structure, streams and separate OCR/extraction provenance.",
            media_graph,
            ("media",),
            MEDIA_MIGRATIONS,
            mmd_filename="media.mmd",
            dot_filename="media.dot",
            pointer_filename="media.pointer.json",
            pointer=media_pointer,
            supports_query=True,
            node_kinds=(
                "media",
                *("media_" + name for name in KINDS),
                "media_ocr_run",
                "media_ocr_line",
                "media_review_frame",
                "media_extraction",
            ),
            edge_kinds=("CONTAINS", "DERIVED_FROM", "OCR_EVIDENCE", "REQUIRES_REVIEW"),
            head_reader=media_heads,
        )
    )

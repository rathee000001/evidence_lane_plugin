"""PDF page, form and OCR evidence locators with distinct graph artifacts."""

from .lane_contract import LaneView, ViewGraph
from .pdf_schema import PDF_MIGRATIONS

KINDS = ("page", "text_block", "image", "table", "form_field", "widget", "outline", "attachment")


def pdf_heads(store):
    from .pdf_profile import current_pdfs

    current = current_pdfs(store)
    if not current["initialized"]:
        return {"pdf_ocr": current}
    with store.lane("pdf_ocr").connection(read_only=True) as connection:
        derived = {
            kind: [
                dict(row)
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY created_at DESC LIMIT 129"
                ).fetchall()
            ]
            for kind, table in [
                ("ocr", "pdf_ocr_run"),
                ("render", "pdf_render"),
                ("docling", "docling_extraction"),
            ]
        }
    return {"pdf_ocr": current, "derivatives": derived}


def pdf_graph(store, scope):
    from .pdf_derivatives import manifest as derivative_manifest
    from .pdf_profile import current_pdfs, read_snapshot

    graph = ViewGraph(store.project_id, scope)
    current = current_pdfs(store)
    for row in current["pdfs"]:
        if scope.query and scope.query != row["pdf_id"]:
            continue
        manifest, facts = read_snapshot(store, row["snapshot_id"])
        root = graph.node(
            "pdf",
            row["snapshot_id"],
            manifest["logical_name"],
            locator={
                "snapshot_id": row["snapshot_id"],
                "pdf_id": row["pdf_id"],
                "sha256": manifest["raw_object"],
            },
        )
        pages, fields, nodes = {}, {}, []
        for item in facts["items"]:
            if item["kind"] not in KINDS:
                continue
            node = graph.node(
                "pdf_" + item["kind"],
                row["snapshot_id"] + ":" + item["item_id"],
                str(item.get("name") or item["text"] or item["kind"])[:100],
                locator={
                    "snapshot_id": row["snapshot_id"],
                    "item_id": item["item_id"],
                    "page": item["page"],
                    "part": item["part"],
                    "kind": item["kind"],
                },
            )
            nodes.append((item, node))
            if item["kind"] == "page":
                pages[item["page"]] = node
            if item["kind"] == "form_field":
                fields[item["name"]] = node
            if graph.truncated:
                break
        for item, node in nodes:
            graph.edge(
                root if item["kind"] == "page" else pages.get(item["page"], root), node, "CONTAINS"
            )
            if item["kind"] == "widget" and item["name"] in fields:
                graph.edge(fields[item["name"]], node, "DISPLAYED_BY")
        if graph.truncated:
            break
        with store.lane("pdf_ocr").connection(read_only=True) as connection:
            runs = connection.execute(
                "SELECT ocr_id FROM pdf_ocr_run WHERE snapshot_id=? ORDER BY created_at DESC LIMIT 33",
                (row["snapshot_id"],),
            ).fetchall()
        for run in runs[:32]:
            body = derivative_manifest(store, run["ocr_id"], "ocr")
            node = graph.node(
                "pdf_ocr_run",
                run["ocr_id"],
                "Separate page OCR",
                locator={"snapshot_id": row["snapshot_id"], "ocr_id": run["ocr_id"]},
            )
            graph.edge(root, node, "DERIVED_FROM")
            for line in body["result"]["lines"]:
                line_node = graph.node(
                    "pdf_ocr_line",
                    run["ocr_id"] + ":" + line["line_id"],
                    line["text"][:100],
                    locator={
                        "ocr_id": run["ocr_id"],
                        "line_id": line["line_id"],
                        "page": line["page"],
                        "review_required": line["review_required"],
                    },
                )
                graph.edge(node, line_node, "CONTAINS")
                if line["page"] in pages:
                    graph.edge(pages[line["page"]], line_node, "OCR_EVIDENCE")
                if graph.truncated:
                    break
            if graph.truncated:
                break
        graph.truncated |= len(runs) > 32
        if graph.truncated:
            break
    graph.truncated |= current.get("truncated", False)
    return graph.result()


def pdf_pointer(binding, graph, files):
    return {
        "schema": "evidence-lane.pdf-page-locators.v4",
        "snapshot_binding": binding,
        "locators": {
            node["key"]: {"node_id": node["id"], **node["locator"]} for node in graph["nodes"]
        },
        "artifacts": files,
        "native_and_ocr_evidence_separate": True,
        "ocr_accuracy_verified": False,
        "rendered_page_visual_review": "separate_operation",
    }


def register_pdf_views(engine):
    engine.registry.register_view(
        LaneView(
            "pdf_ocr.structure",
            "pdf_ocr",
            "pdf_ocr",
            "Page, native form/widget and separate OCR evidence locators.",
            pdf_graph,
            ("pdfocr",),
            PDF_MIGRATIONS,
            mmd_filename="pdf.mmd",
            dot_filename="pdf.dot",
            pointer_filename="pdf.pointer.json",
            pointer=pdf_pointer,
            supports_query=True,
            node_kinds=("pdf", *("pdf_" + name for name in KINDS), "pdf_ocr_run", "pdf_ocr_line"),
            edge_kinds=("CONTAINS", "DISPLAYED_BY", "DERIVED_FROM", "OCR_EVIDENCE"),
            head_reader=pdf_heads,
        )
    )

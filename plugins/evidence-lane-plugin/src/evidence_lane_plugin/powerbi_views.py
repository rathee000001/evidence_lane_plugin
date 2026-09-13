"""Power BI metadata locators; these derived graphs are not rendered report charts."""

from pathlib import PurePosixPath

from .lane_contract import LaneView, ViewGraph
from .powerbi_schema import POWERBI_MIGRATIONS

GRAPH_KINDS = (
    "project",
    "report",
    "page",
    "visual",
    "model",
    "table",
    "column",
    "measure",
    "relationship",
    "reference",
    "data_source",
    "hierarchy",
)


def powerbi_graph(store, scope):
    from .powerbi_profile import current_powerbis, read_snapshot

    graph = ViewGraph(store.project_id, scope)
    current = current_powerbis(store)
    for row in current["powerbis"]:
        if scope.query and scope.query != row["powerbi_id"]:
            continue
        manifest, facts = read_snapshot(store, row["snapshot_id"])
        root = graph.node(
            "powerbi",
            row["snapshot_id"],
            manifest["logical_name"],
            locator={
                "snapshot_id": row["snapshot_id"],
                "powerbi_id": row["powerbi_id"],
                "sha256": manifest["raw_object"],
            },
        )
        tables, pages, models, reports, nodes = {}, {}, {}, {}, []
        for item in facts["items"]:
            if item["kind"] not in GRAPH_KINDS:
                continue
            label = str(item.get("displayName") or item.get("name") or item["kind"])[:120]
            node = graph.node(
                "powerbi_" + item["kind"],
                row["snapshot_id"] + ":" + item["item_id"],
                label,
                locator={
                    "snapshot_id": row["snapshot_id"],
                    "item_id": item["item_id"],
                    "part": item["part"],
                    "kind": item["kind"],
                    "locator": item["locator"],
                    "locator_basis": item["locator_basis"],
                },
            )
            nodes.append((item, node))
            if item["kind"] == "table":
                tables[(item["part"], item.get("name"))] = node
            elif item["kind"] == "page":
                pages[(item["part"], item["locator"])] = node
            elif item["kind"] == "model":
                models[item["part"]] = node
            elif item["kind"] == "report":
                reports[item["part"]] = node
            if graph.truncated:
                break
        # Resolve parents after collecting nodes: PBIX metadata columns precede
        # their tables, while PBIR pages and visuals live in distinct members.
        for item, node in nodes:
            part, kind = item["part"], item["kind"]
            parent = root
            if kind not in {"model", "report", "project"}:
                parent = models.get(part) or reports.get(part) or root
            if item.get("table_name"):
                parent = tables.get((part, item["table_name"]), parent)
            if kind == "visual":
                if "/visualContainers/" in item["locator"]:
                    key = (part, item["locator"].split("/visualContainers/")[0])
                else:
                    path = PurePosixPath(part)
                    key = ((path.parent.parent.parent / "page.json").as_posix(), "/")
                parent = pages.get(key, parent)
            if kind == "page" and "/definition/pages/" in part:
                report = part.split("/definition/pages/")[0] + "/definition/report.json"
                parent = reports.get(report, parent)
            graph.edge(parent, node, "CONTAINS")
        if graph.truncated:
            break
    graph.truncated |= current.get("truncated", False)
    return graph.result()


def powerbi_pointer(binding, graph, files):
    return {
        "schema": "evidence-lane.powerbi-locators.v4",
        "snapshot_binding": binding,
        "locators": {
            node["key"]: {"node_id": node["id"], **node["locator"]} for node in graph["nodes"]
        },
        "artifacts": files,
        "stored_model_rows_are_bounded_samples": True,
        "report_rendering_verified": False,
        "dax_execution": False,
    }


def register_powerbi_views(engine):
    from .powerbi_profile import current_powerbis

    engine.registry.register_view(
        LaneView(
            "power_bi.structure",
            "power_bi",
            "power_bi",
            "Exact project/report/model metadata locators and native stored model structure; not rendered charts.",
            powerbi_graph,
            ("powerbi",),
            POWERBI_MIGRATIONS,
            mmd_filename="powerbi.mmd",
            dot_filename="powerbi.dot",
            pointer_filename="powerbi.pointer.json",
            pointer=powerbi_pointer,
            supports_query=True,
            node_kinds=("powerbi", *("powerbi_" + name for name in GRAPH_KINDS)),
            edge_kinds=("CONTAINS",),
            head_reader=lambda store: {"power_bi": current_powerbis(store)},
        )
    )

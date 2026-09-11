"""Bounded AcroForm and page-widget inspection without evaluating PDF actions."""

from __future__ import annotations

from .errors import LaneError


def fail(code):
    raise LaneError("PDF_" + code, "The PDF does not satisfy this bounded form operation.")


def deref(value):
    return value.get_object() if hasattr(value, "get_object") else value


def reference(value):
    ref = getattr(value, "indirect_reference", value)
    return f"{ref.idnum}:{ref.generation}" if hasattr(ref, "idnum") else None


def scalar(value):
    from pypdf.generic import BooleanObject

    value = deref(value)
    if isinstance(value, BooleanObject):
        return value.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return str(value) if isinstance(value, str) else value
    if isinstance(value, (list, tuple)):
        if len(value) > 100:
            fail("FORM_VALUE_BUDGET")
        return [scalar(item) for item in value]
    return {"reference": reference(value), "type": type(value).__name__}


def inspect_forms(reader):
    root = reader.root_object
    acro = deref(root.get("/AcroForm", {}))
    nodes, visiting, by_ref, terminals = [], set(), {}, {}

    def walk(raw, parent_name="", inherited=None, depth=0):
        node = deref(raw)
        key = reference(raw)
        if not isinstance(node, dict) or key is None or depth > 32 or len(nodes) >= 5000:
            fail("FORM_TREE_BUDGET")
        if key in visiting or key in by_ref:
            fail("FORM_TREE_AMBIGUOUS")
        visiting.add(key)
        state = dict(inherited or {})
        state.update(
            {
                name: node[name]
                for name in ("/FT", "/Ff", "/V", "/DV", "/Opt", "/MaxLen")
                if name in node
            }
        )
        local_name = str(node.get("/T", ""))
        name = ".".join(part for part in (parent_name, local_name) if part)
        if len(name) > 500 or len(local_name) > 180:
            fail("FORM_NAME_BUDGET")
        kids = deref(node.get("/Kids", []))
        if not isinstance(kids, list) or len(kids) > 5000:
            fail("FORM_TREE_BUDGET")
        field_children = [
            child
            for child in kids
            if "/T" in deref(child) or deref(child).get("/Subtype") != "/Widget"
        ]
        terminal = bool(state.get("/FT")) and not field_children and bool(name)
        row = {
            "reference": key,
            "name": name,
            "parent_name": parent_name,
            "terminal": terminal,
            "field_type": str(state.get("/FT", "")),
            "flags": int(state.get("/Ff", 0)),
            "value": scalar(state.get("/V")),
            "default_value": scalar(state.get("/DV")),
            "options": scalar(state.get("/Opt", [])),
            "max_length": int(state["/MaxLen"]) if "/MaxLen" in state else None,
            "widget_references": [],
            "has_action": "/AA" in node or "/A" in node,
            "signed": False,
        }
        if row["field_type"] == "/Sig" and state.get("/V"):
            signature = deref(state["/V"])
            row["signed"] = isinstance(signature, dict) and "/ByteRange" in signature
        if node.get("/Subtype") == "/Widget":
            row["widget_references"].append(key)
        row["widget_references"] += [
            reference(child)
            for child in kids
            if deref(child).get("/Subtype") == "/Widget" and "/T" not in deref(child)
        ]
        by_ref[key] = row
        nodes.append(row)
        if terminal:
            if name in terminals:
                fail("FORM_DUPLICATE_NAME")
            terminals[name] = row
        for child in field_children:
            walk(child, name, state, depth + 1)
        visiting.remove(key)

    fields = deref(acro.get("/Fields", []))
    if not isinstance(fields, list) or len(fields) > 5000:
        fail("FORM_TREE_BUDGET")
    for raw in fields:
        walk(raw)
    widget_owner = {}
    for row in nodes:
        for ref in row["widget_references"]:
            if ref is None or ref in widget_owner:
                fail("FORM_WIDGET_AMBIGUOUS")
            widget_owner[ref] = row
    widgets, seen = [], set()
    for page_number, page in enumerate(reader.pages, 1):
        annots = deref(page.get("/Annots", []))
        if not isinstance(annots, list) or len(annots) > 5000:
            fail("ANNOTATION_BUDGET")
        for raw in annots:
            widget = deref(raw)
            if widget.get("/Subtype") != "/Widget":
                continue
            key = reference(raw)
            if key is None or key in seen or len(widgets) >= 5000:
                fail("FORM_WIDGET_AMBIGUOUS")
            seen.add(key)
            owner = widget_owner.get(key)
            appearance = deref(widget.get("/AP", {}))
            normal = deref(appearance.get("/N"))
            states = (
                sorted(str(key) for key in normal)
                if isinstance(normal, dict) and not hasattr(normal, "get_data")
                else []
            )
            rect = scalar(widget.get("/Rect", []))
            widgets.append(
                {
                    "reference": key,
                    "page": page_number,
                    "name": owner["name"] if owner else str(widget.get("/T", "")),
                    "canonical_reference": owner["reference"] if owner else None,
                    "orphan": owner is None,
                    "field_type": owner["field_type"] if owner else str(widget.get("/FT", "")),
                    "value": owner["value"] if owner else scalar(widget.get("/V")),
                    "widget_value": scalar(widget.get("/V")),
                    "rect_pdf": rect,
                    "appearance_present": normal is not None,
                    "appearance_states": states,
                    "appearance_state": scalar(widget.get("/AS")),
                    "has_action": "/AA" in widget or "/A" in widget,
                    "parent_reference": reference(widget.get("/Parent")),
                }
            )
    missing = sorted(set(widget_owner) - seen)
    return {
        "fields": nodes,
        "widgets": widgets,
        "missing_page_widgets": missing,
        "xfa": "/XFA" in acro,
        "need_appearances": scalar(acro.get("/NeedAppearances", False)) is True,
        "signed": any(row["signed"] for row in nodes),
        "actions_executed": False,
    }


def repair_orphans(writer):
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject

    inspection = inspect_forms(writer)
    orphans = [row for row in inspection["widgets"] if row["orphan"]]
    names = {row["name"] for row in inspection["fields"] if row["terminal"]}
    for row in orphans:
        if (
            not row["name"]
            or row["name"] in names
            or row["parent_reference"]
            or not row["field_type"]
        ):
            fail("ORPHAN_REPAIR_AMBIGUOUS")
        names.add(row["name"])
    acro = deref(writer.root_object.get("/AcroForm"))
    if acro is None:
        acro = DictionaryObject({NameObject("/Fields"): ArrayObject()})
        writer.root_object[NameObject("/AcroForm")] = writer._add_object(acro)
    if "/Fields" not in acro:
        acro[NameObject("/Fields")] = ArrayObject()
    targets = {row["reference"] for row in orphans}
    for page in writer.pages:
        for raw in deref(page.get("/Annots", [])):
            if reference(raw) in targets:
                acro["/Fields"].append(raw)
    return len(orphans)

"""Portable MCP Apps UI resources for governed Evidence Lane status views."""

from __future__ import annotations

import html
import json
from typing import Any

from .constants import ENGINE_VERSION

MCP_APP_MIME_TYPE = "text/html;profile=mcp-app"
# MCP Apps hosts may cache UI resources by immutable ``ui://`` identity.  Bump
# the resource URI whenever the embedded view contract changes so a host cannot
# pair a new tool result with an older cached bridge implementation.
GOVERNED_PANEL_URI = "ui://evidence-lane/governed-console-v2.html"


def governed_panel_resource_meta(public_site_url: str) -> dict[str, Any]:
    """Return the narrow resource policy advertised to MCP Apps hosts."""

    exact_site = public_site_url.rstrip("/")
    return {
        "ui": {
            "prefersBorder": True,
            "domain": exact_site,
            "csp": {
                "connectDomains": [],
                "resourceDomains": [],
            },
        }
    }


def governed_panel_tool_meta(label: str, done: str) -> dict[str, Any]:
    """Associate only explicit render tools with the governed panel."""

    return {
        "ui": {
            "resourceUri": GOVERNED_PANEL_URI,
            "visibility": ["model", "app"],
        },
        "openai/outputTemplate": GOVERNED_PANEL_URI,
        "openai/toolInvocation/invoking": label,
        "openai/toolInvocation/invoked": done,
    }


def _link(label: str, href: str) -> dict[str, str]:
    return {"label": label, "href": href}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def build_runtime_panel_snapshot(
    *,
    doctor: dict[str, Any],
    lane_catalog: dict[str, Any],
    public_site_url: str,
) -> dict[str, Any]:
    """Project a secret-free runtime and lane snapshot for the UI."""

    engine = _as_dict(doctor.get("engine"))
    checks = _as_dict(doctor.get("checks"))
    route = _as_dict(doctor.get("mcp_route_identity"))
    raw_lanes = _as_list(lane_catalog.get("lanes"))
    lanes = [
        {
            "id": str(item.get("canonical_lane_id") or ""),
            "name": str(item.get("display_label") or ""),
            "parser": str(item.get("parser_id") or ""),
        }
        for item in raw_lanes
        if isinstance(item, dict)
    ]
    exact_site = public_site_url.rstrip("/")
    return {
        "schema": "evidence-lane.mcp-app-panel.v1",
        "panel": "runtime",
        "status": str(doctor.get("status") or "UNKNOWN"),
        "title": "Evidence Lane runtime",
        "summary": (
            "Verified runtime identity, host checks, and the canonical lane "
            "registry. This panel does not mutate a project or move a pointer."
        ),
        "facts": [
            {"label": "Release", "value": str(engine.get("release") or "UNKNOWN")},
            {"label": "Engine commit", "value": str(engine.get("commit") or "UNKNOWN")},
            {
                "label": "Canonical lanes",
                "value": str(lane_catalog.get("lane_count") or len(lanes)),
            },
            {
                "label": "Native MCP route",
                "value": str(route.get("server_identity") or "UNVERIFIED"),
            },
            {
                "label": "Tool catalog",
                "value": (
                    f"{route.get('tool_count') or 0} unique tools / "
                    f"{route.get('status') or 'BLOCKED'}"
                ),
            },
            {
                "label": "Runtime checks",
                "value": f"{sum(bool(value) for value in checks.values())}/{len(checks)} pass",
            },
        ],
        "lanes": lanes,
        "hil": None,
        "links": [
            _link("Website", exact_site),
            _link("Privacy", f"{exact_site}/privacy"),
            _link("Terms", f"{exact_site}/terms"),
            _link("Support", f"{exact_site}/support"),
            _link("License", f"{exact_site}/license"),
            _link("Copyright", f"{exact_site}/copyright"),
        ],
        "read_only": True,
    }


def build_project_panel_snapshot(
    *,
    project_id: str,
    project_status: dict[str, Any],
    public_site_url: str,
) -> dict[str, Any]:
    """Project only public-safe pointer and HIL facts for one project."""

    envelope = _as_dict(project_status.get("persistent_state_envelope"))
    session = _as_dict(project_status.get("active_session"))
    project_route = _as_dict(project_status.get("project_route"))
    exact_site = public_site_url.rstrip("/")
    accepted_pv = envelope.get("accepted_pv")
    candidate = envelope.get("pending_candidate")
    pending_hil = bool(envelope.get("pending_hil"))
    return {
        "schema": "evidence-lane.mcp-app-panel.v1",
        "panel": "project",
        "status": str(project_status.get("status") or "UNKNOWN"),
        "title": "Governed project status",
        "summary": (
            "Read-only accepted pointer, active-session, candidate, and HIL "
            "boundary. Rendering this panel never accepts or promotes a candidate."
        ),
        "facts": [
            {"label": "Project", "value": project_id},
            {"label": "Accepted PV", "value": str(accepted_pv or "NONE")},
            {
                "label": "Pointer generation",
                "value": str(envelope.get("pointer_generation") or 0),
            },
            {"label": "Active state", "value": str(session.get("state") or "NONE")},
            {"label": "Pending candidate", "value": str(candidate or "NONE")},
            {
                "label": "Project route",
                "value": str(
                    project_route.get("relative_project_route")
                    or f"projects/{project_id}"
                ),
            },
            {
                "label": "Storage mode",
                "value": str(project_route.get("storage_mode") or "AUTO"),
            },
            {
                "label": "Host profile",
                "value": str(project_route.get("active_host_profile") or "INACTIVE"),
            },
        ],
        "lanes": [],
        "hil": {
            "pending": pending_hil,
            "candidate": candidate,
            "rule": "Only an exact governed HIL decision can change authority.",
        },
        "links": [
            _link("Website", exact_site),
            _link("Proof", f"{exact_site}/proof"),
            _link("Architecture", f"{exact_site}/architecture"),
            _link("Support", f"{exact_site}/support"),
        ],
        "read_only": True,
    }


def governed_panel_html(public_site_url: str) -> str:
    """Return one dependency-free, CSP-minimal MCP Apps component."""

    exact_site = public_site_url.rstrip("/")
    safe_site = html.escape(exact_site, quote=True)
    site_json = json.dumps(exact_site)
    version_json = json.dumps(ENGINE_VERSION)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Evidence Lane governed console</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 14px; background: transparent; color: CanvasText; }}
    .shell {{ border: 1px solid color-mix(in srgb, CanvasText 14%, transparent); border-radius: 22px;
      background: color-mix(in srgb, Canvas 82%, transparent); box-shadow: 0 18px 54px rgba(0,0,0,.12); overflow: hidden; }}
    header {{ display: flex; align-items: center; gap: 12px; padding: 16px 18px 12px; }}
    .orb {{ width: 42px; height: 42px; display: grid; place-items: center; border-radius: 14px;
      background: linear-gradient(145deg,#13b8d4,#6657e8); color: white; font-weight: 800; }}
    h1 {{ font-size: 16px; margin: 0; }}
    #summary {{ margin: 3px 0 0; opacity: .72; font-size: 12px; line-height: 1.45; }}
    .status {{ margin-left: auto; border-radius: 999px; padding: 6px 10px; font-size: 11px; font-weight: 800;
      background: color-mix(in srgb,#22c55e 18%,transparent); color: color-mix(in srgb,#22c55e 84%,CanvasText); }}
    nav {{ display: flex; gap: 8px; padding: 0 18px 12px; flex-wrap: wrap; }}
    button {{ border: 1px solid color-mix(in srgb, CanvasText 16%, transparent); border-radius: 999px; padding: 7px 11px;
      background: color-mix(in srgb, Canvas 86%, transparent); color: inherit; cursor: pointer; }}
    button[aria-selected="true"] {{ background: linear-gradient(135deg,#13b8d4,#6657e8); color: white; border-color: transparent; }}
    main {{ padding: 0 18px 16px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(150px,1fr)); gap: 8px; }}
    .card {{ border: 1px solid color-mix(in srgb, CanvasText 12%, transparent); border-radius: 14px; padding: 11px;
      background: color-mix(in srgb, Canvas 78%, transparent); min-width: 0; }}
    .label {{ font-size: 10px; text-transform: uppercase; letter-spacing: .08em; opacity: .62; }}
    .value {{ margin-top: 5px; font-size: 12px; overflow-wrap: anywhere; }}
    .empty {{ padding: 14px; opacity: .66; border: 1px dashed color-mix(in srgb,CanvasText 18%,transparent); border-radius: 14px; }}
    footer {{ display: flex; flex-wrap: wrap; gap: 9px; padding: 12px 18px 16px; border-top: 1px solid color-mix(in srgb,CanvasText 10%,transparent); }}
    a {{ color: inherit; font-size: 11px; text-underline-offset: 3px; }}
  </style>
</head>
<body>
  <section class="shell" aria-live="polite">
    <header><div class="orb" aria-hidden="true">EL</div><div><h1 id="title">Evidence Lane</h1><p id="summary">Waiting for a governed tool result.</p></div><span class="status" id="status">READY</span></header>
    <nav aria-label="Evidence Lane panels">
      <button type="button" data-tab="overview" aria-selected="true">Overview</button>
      <button type="button" data-tab="lanes" aria-selected="false">Lanes</button>
      <button type="button" data-tab="hil" aria-selected="false">HIL</button>
    </nav>
    <main id="content"><div class="empty">Call a dedicated Evidence Lane render tool to load verified data.</div></main>
    <footer id="links"><a href="{safe_site}" target="_blank" rel="noreferrer">Website</a></footer>
  </section>
  <script>
    (() => {{
      "use strict";
      const publicSite = {site_json};
      const appVersion = {version_json};
      const initializeRequestId = "evidence-lane-ui-initialize-1";
      let snapshot = null;
      let snapshotFingerprint = "";
      let activeTab = "overview";
      let initialized = false;
      const title = document.getElementById("title");
      const summary = document.getElementById("summary");
      const status = document.getElementById("status");
      const content = document.getElementById("content");
      const links = document.getElementById("links");
      const buttons = [...document.querySelectorAll("[data-tab]")];

      function el(tag, className, text) {{
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
      }}
      function cards(items) {{
        const grid = el("div", "grid");
        (Array.isArray(items) ? items : []).forEach((item) => {{
          const card = el("div", "card");
          card.append(el("div", "label", item?.label ?? item?.name ?? item?.id ?? "Item"));
          card.append(el("div", "value", item?.value ?? item?.parser ?? ""));
          grid.append(card);
        }});
        return grid.childElementCount ? grid : el("div", "empty", "No governed records in this panel.");
      }}
      function normalize(raw) {{
        return raw && typeof raw === "object" && raw.data && typeof raw.data === "object" ? raw.data : raw;
      }}
      function snapshotFromResult(raw) {{
        const normalized = normalize(raw);
        if (normalized && typeof normalized === "object" && normalized.structuredContent && typeof normalized.structuredContent === "object") {{
          return normalize(normalized.structuredContent);
        }}
        return normalized;
      }}
      function acceptSnapshot(raw, rerender = true) {{
        const next = snapshotFromResult(raw);
        if (!next || typeof next !== "object") return false;
        let fingerprint = "";
        try {{ fingerprint = JSON.stringify(next); }} catch (_) {{}}
        if (fingerprint && fingerprint === snapshotFingerprint) return false;
        snapshot = next;
        snapshotFingerprint = fingerprint;
        if (rerender) render();
        return true;
      }}
      function render() {{
        const data = snapshot || {{}};
        title.textContent = String(data.title || "Evidence Lane");
        summary.textContent = String(data.summary || "Waiting for a governed tool result.");
        status.textContent = String(data.status || "READY").slice(0, 32);
        content.replaceChildren();
        if (activeTab === "lanes") {{
          content.append(cards(data.lanes));
        }} else if (activeTab === "hil") {{
          const hil = data.hil;
          content.append(hil ? cards([
            {{label: "Pending", value: hil.pending ? "YES" : "NO"}},
            {{label: "Candidate", value: hil.candidate || "NONE"}},
            {{label: "Authority rule", value: hil.rule || "Exact HIL decision required"}},
          ]) : el("div", "empty", "No project HIL data was requested."));
        }} else {{
          content.append(cards(data.facts));
        }}
        links.replaceChildren();
        const safeLinks = Array.isArray(data.links) ? data.links : [{{label: "Website", href: publicSite}}];
        safeLinks.forEach((item) => {{
          try {{
            const url = new URL(String(item.href || ""));
            if (url.protocol !== "https:") return;
            const anchor = el("a", "", item.label || url.hostname);
            anchor.href = url.href;
            anchor.target = "_blank";
            anchor.rel = "noreferrer";
            links.append(anchor);
          }} catch (_) {{}}
        }});
      }}
      function selectTab(tab) {{
        activeTab = tab;
        buttons.forEach((button) => button.setAttribute("aria-selected", String(button.dataset.tab === tab)));
        const openai = typeof window !== "undefined" ? window.openai : undefined;
        openai?.setWidgetState?.({{ activeTab }});
        render();
      }}
      function postToHost(message) {{
        window.parent.postMessage(message, "*");
      }}
      function onHostMessage(event) {{
        if (event.source !== window.parent) return;
        const message = event.data;
        if (!message || message.jsonrpc !== "2.0") return;
        if (message.id === initializeRequestId) {{
          if (initialized || !Object.prototype.hasOwnProperty.call(message, "result")) return;
          initialized = true;
          postToHost({{
            jsonrpc: "2.0",
            method: "ui/notifications/initialized",
            params: {{}},
          }});
          return;
        }}
        if (message.method === "ui/notifications/tool-result") {{
          acceptSnapshot(message.params);
        }}
      }}
      buttons.forEach((button) => button.addEventListener("click", () => selectTab(button.dataset.tab)));
      const openai = typeof window !== "undefined" ? window.openai : undefined;
      if (openai?.widgetState?.activeTab) activeTab = String(openai.widgetState.activeTab);
      if (openai?.toolOutput) acceptSnapshot(openai.toolOutput, false);
      window.addEventListener("message", onHostMessage, {{ passive: true }});
      postToHost({{
        jsonrpc: "2.0",
        id: initializeRequestId,
        method: "ui/initialize",
        params: {{
          appInfo: {{ name: "Evidence Lane", version: appVersion }},
          appCapabilities: {{}},
          protocolVersion: "2026-01-26",
        }},
      }});
      selectTab(activeTab);
    }})();
  </script>
</body>
</html>"""

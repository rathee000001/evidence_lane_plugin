"""Read-only MCP Apps views over the persistent v4 engine and separate lanes.

Adapts the original status cards and host bridge. Plan and session state are
observations; rendering never changes project work or infers native authority.
"""
from __future__ import annotations

import base64
import html
import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Final, Literal, cast

from pydantic import Field, JsonValue

from . import __version__
from .lanes import LANE_REGISTRY
from .plan_runtime import PLAN_MIGRATIONS, PlanRead, PlanStore
from .registry import ActionSpec, Contract
from .session_authority import SESSION_MIGRATIONS, SessionStatusRequest
from .storage import bounded_project_read, now
from .universe_snapshot import root_reference

MCP_APP_MIME_TYPE = 'text/html;profile=mcp-app'
GOVERNED_PANEL_URI = 'ui://evidence-lane/governed-console-v4-1.html'
PANEL_SCHEMA: Final = 'evidence-lane.mcp-app-panel.v4'
PUBLIC_SITE_URL = 'https://evidencelane.org'


class PanelRead(Contract):
    pass


class ProjectPanelRead(Contract):
    offset: int = Field(default=0, ge=0, le=10000)
    limit: int = Field(default=20, ge=1, le=100)


class PanelFact(Contract):
    label: str
    value: str


class PanelLane(Contract):
    id: str
    name: str
    kind: Literal['authority', 'sector']
    state: Literal['registered', 'published', 'initialized_without_publication']
    revision: int | None = None
    head_digest: str | None = None


class PanelSnapshot(Contract):
    panel_schema: Literal['evidence-lane.mcp-app-panel.v4'] = PANEL_SCHEMA
    panel: Literal['runtime', 'project']
    title: str
    summary: str
    status: Literal['observed'] = 'observed'
    observed_at: str
    identity: dict[str, JsonValue]
    project_id: str | None = None
    facts: list[PanelFact]
    lanes: list[PanelLane]
    plan: dict[str, JsonValue] | None = None
    session: dict[str, JsonValue] | None = None
    root_pv: dict[str, JsonValue] | None = None
    links: list[dict[str, str]]
    read_only: Literal[True] = True
    native_task_attestation: Literal['not_provided'] = 'not_provided'
    native_goal_completed: Literal[False] = False
    mutation_performed: Literal[False] = False


def governed_panel_resource_meta() -> dict:
    return {'ui': {'prefersBorder': True, 'csp': {'connectDomains': [], 'resourceDomains': []}}}


def _identity() -> dict:
    return {'display_name': 'Evidence Lane', 'server_identity': 'evidence-lane-plugin',
            'release': __version__, 'website_url': PUBLIC_SITE_URL}


def _links(*, project=False) -> list[dict[str, str]]:
    paths = (('Website', ''), ('Proof', '/proof'), ('Architecture', '/architecture'), ('Support', '/support')) if project else (
        ('Website', ''), ('Privacy', '/privacy'), ('Terms', '/terms'), ('Support', '/support'),
        ('License', '/license'), ('Copyright', '/copyright'))
    return [{'label': label, 'href': PUBLIC_SITE_URL + path} for label, path in paths]


def _fact(label, value):
    return PanelFact(label=label, value='not_recorded' if value is None else str(value))


def build_runtime_panel_snapshot(engine, context) -> PanelSnapshot:
    doctor = engine.sessions.doctor(context, SessionStatusRequest())
    inventory = engine.capabilities.snapshot()
    host = doctor.host
    return PanelSnapshot(panel='runtime', title='Evidence Lane runtime', observed_at=now(),
        summary='Current engine, Flash and host observations. Catalog presence does not establish tool execution or full installation readiness.',
        identity=_identity(), links=_links(), facts=[
            _fact('Engine phase', doctor.engine_phase), _fact('Release', __version__),
            _fact('Engine instance', doctor.engine_instance_id), _fact('Package digest', doctor.package_digest),
            _fact('Locked Flash', doctor.flash.state), _fact('Engine platform', host['operating_system']),
            _fact('Configured client profile (reported)', host['client']['configured_profile']),
            _fact('Shared toolchain', doctor.managed_toolchain),
            _fact('Inventory observed at', inventory.get('observed_at')),
            _fact('Tool inventory', ', '.join(str(item['name']) + ': ' + str(item['state']) for item in inventory.get('tools', [])) or 'not_probed'),
            _fact('Provider probe results', ', '.join(str(item.get('runtime_id')) + ': ' + str(item.get('self_test')) for item in inventory.get('provider_probes', [])) or 'not_probed'),
            _fact('Registered actions', len(engine.registry.schemas())),
            _fact('Native task attestation', doctor.native_task_attestation)],
        lanes=[PanelLane(id=lane.canonical_lane_id, name=lane.display_label, kind=lane.kind, state='registered')
               for lane in sorted(LANE_REGISTRY.values(), key=lambda item: item.canonical_lane_id)])


def build_project_panel_snapshot(engine, context, request) -> PanelSnapshot:
    project = engine.directory.open(context.project_id)
    with bounded_project_read(project.root, time.monotonic() + 10):
        project.assert_current_binding()
        head = root_reference(project)
        registration = project.registration
        plan = PlanStore(project).snapshot(PlanRead(offset=request.offset, limit=request.limit))
        session = engine.sessions.status(context, SessionStatusRequest())
        lanes = []
        for entry in project.lane_catalog():
            lane = project.lane(entry['lane_id'])
            lanes.append(PanelLane(id=lane.lane_id, name=lane.definition.display_label,
                kind=lane.definition.kind, state='published' if entry['head_digest'] else 'initialized_without_publication',
                revision=entry['revision'], head_digest=entry['head_digest']))
        return PanelSnapshot(panel='project', project_id=project.project_id, title=registration['display_name'],
            observed_at=now(), identity=_identity(), links=_links(project=True), root_pv=head,
            summary='One selected project, read at one published project evidence head coordinator. Plan, session and lane references retain their separate owners.',
            facts=[_fact('Project', project.project_id), _fact('Sensitivity label', registration['sensitivity']),
                _fact('Sensitivity enforcement', registration['sensitivity_enforcement']),
                _fact('Capture route', registration['capture_route']), _fact('project evidence head coordinator revision', head['revision']),
                _fact('project evidence head coordinator digest', head['head_digest'] or 'initial'), _fact('Plan revision', plan.revision),
                _fact('Plan tasks', plan.total_tasks), _fact('Session', session.state),
                _fact('Session owner connected', session.owner_authenticated), _fact('Capture bound', session.capture_bound),
                _fact('Session Flash current', session.flash_current)],
            lanes=lanes,
            plan={'state': plan.state, 'title': plan.title, 'revision': plan.revision,
                'document_digest': plan.document_digest, 'event_head': plan.event_head,
                'total_tasks': plan.total_tasks, 'counts': plan.counts, 'offset': plan.offset,
                'truncated': plan.truncated, 'authority': 'plan_lane_sqlite',
                'tasks': [{'position': row.position, 'task_id': row.definition.task_id,
                    'title': row.definition.title, 'state': row.state, 'contract_digest': row.contract_digest,
                    'dependencies': list(cast(list[str], row.definition.dependencies))} for row in plan.tasks]},
            session={key: getattr(session, key) for key in ('state', 'session_id', 'generation',
                'owner_client_id', 'owner_engine_id', 'owner_authenticated', 'event_digest',
                'capture_bound', 'flash_current', 'state_authority', 'native_task_attestation')})


def register_panel_actions(engine):
    engine.registry.register(ActionSpec('render_runtime_panel',
        'Render read-only engine, Flash, reported host and lane-catalog observations; structured data remains available without an Apps renderer.',
        PanelRead, PanelSnapshot, lambda context, request: build_runtime_panel_snapshot(engine, context),
        workflow='open-project-session', project_required=False, ui_resource=GOVERNED_PANEL_URI))
    engine.registry.register(ActionSpec('render_project_panel',
        'Render the selected project registration, current Plan page, session and exact published lane references without changing work.',
        ProjectPanelRead, PanelSnapshot, lambda context, request: build_project_panel_snapshot(engine, context, request),
        workflow='evidence-lane', queryable_in_delta=True, studio_read=True,
        read_migrations=(*PLAN_MIGRATIONS, *SESSION_MIGRATIONS), ui_resource=GOVERNED_PANEL_URI))


@lru_cache(maxsize=1)
def _icon_data() -> str:
    # The existing packaged website asset; no external image request or data URI supplied by a tool result.
    content = (Path(__file__).parent / 'studio/assets/evidence-lane-icon.png').read_bytes()
    if len(content) > 1048576 or not content.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('The packaged panel icon must be a bounded PNG.')
    return 'data:image/png;base64,' + base64.b64encode(content).decode('ascii')


def governed_panel_html() -> str:
    """Return one dependency-free, CSP-minimal MCP Apps component."""

    exact_site = PUBLIC_SITE_URL
    safe_site = html.escape(exact_site, quote=True)
    safe_icon = html.escape(_icon_data(), quote=True)
    site_json = json.dumps(exact_site)
    version_json = json.dumps(__version__)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>Evidence Lane project console</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 14px; background: transparent; color: CanvasText; }}
    .shell {{ border: 1px solid color-mix(in srgb, CanvasText 14%, transparent); border-radius: 22px;
      background: color-mix(in srgb, Canvas 82%, transparent); box-shadow: 0 18px 54px rgba(0,0,0,.12); overflow: hidden; }}
    header {{ display: flex; align-items: center; gap: 12px; padding: 16px 18px 12px; }}
    .orb {{ width: 42px; height: 42px; display: grid; place-items: center; flex: 0 0 auto; border-radius: 14px;
      background: linear-gradient(145deg,#13b8d4,#6657e8); overflow: hidden; }}
    .orb img {{ width: 100%; height: 100%; object-fit: contain; }}
    .brand-name {{ margin-bottom: 2px; font-size: 10px; font-weight: 800; letter-spacing: .09em; text-transform: uppercase; opacity: .68; }}
    header > div {{ min-width: 0; }}
    h1 {{ font-size: 16px; margin: 0; overflow-wrap: anywhere; }}
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
    .label {{ font-size: 10px; text-transform: uppercase; letter-spacing: .08em; opacity: .62; overflow-wrap: anywhere; }}
    .value {{ margin-top: 5px; font-size: 12px; overflow-wrap: anywhere; }}
    .empty {{ padding: 14px; opacity: .66; border: 1px dashed color-mix(in srgb,CanvasText 18%,transparent); border-radius: 14px; }}
    footer {{ display: flex; flex-wrap: wrap; gap: 9px; padding: 12px 18px 16px; border-top: 1px solid color-mix(in srgb,CanvasText 10%,transparent); }}
    a {{ color: inherit; font-size: 11px; text-underline-offset: 3px; }}
  </style>
</head>
<body>
  <section class="shell" aria-live="polite">
    <header><div class="orb"><img src="{safe_icon}" width="42" height="42" alt="Evidence Lane cube icon" /></div><div><div class="brand-name">Evidence Lane</div><h1 id="title">Project console</h1><p id="summary">Waiting for a governed tool result.</p></div><span class="status" id="status">WAITING</span></header>
    <nav aria-label="Evidence Lane panels">
      <button type="button" data-tab="overview" aria-selected="true">Overview</button>
      <button type="button" data-tab="lanes" aria-selected="false">Lanes</button>
      <button type="button" data-tab="plan" aria-selected="false">Plan</button>
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
      let readFailed = false;
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
        if (raw?.status === "error" || raw?.isError) return null;
        return raw?.result && typeof raw.result === "object" ? raw.result : raw;
      }}
      function snapshotFromResult(raw) {{
        const normalized = normalize(raw);
        if (normalized && typeof normalized === "object" && normalized.structuredContent && typeof normalized.structuredContent === "object") {{
          return normalize(normalized.structuredContent);
        }}
        return normalized;
      }}
      function acceptSnapshot(raw, rerender = true) {{
        if (raw?.isError || raw?.status === "error" || raw?.structuredContent?.status === "error") {{
          snapshot = null;
          snapshotFingerprint = "";
          readFailed = true;
          render();
          return false;
        }}
        const next = snapshotFromResult(raw);
        if (!next || next.panel_schema !== "evidence-lane.mcp-app-panel.v4" || next.read_only !== true) return false;
        let fingerprint = "";
        try {{ fingerprint = JSON.stringify(next); }} catch (_) {{}}
        if (fingerprint && fingerprint === snapshotFingerprint) return false;
        snapshot = next;
        snapshotFingerprint = fingerprint;
        readFailed = false;
        if (rerender) render();
        return true;
      }}
      function render() {{
        const data = snapshot || {{}};
        title.textContent = String(data.title || "Evidence Lane");
        summary.textContent = readFailed ? "The latest read failed. Request a new view through the host." : String(data.summary || "Waiting for a governed tool result.");
        status.textContent = readFailed ? "READ FAILED" : String(data.status || "WAITING").slice(0, 32);
        content.replaceChildren();
        if (activeTab === "lanes") {{
          content.append(cards((data.lanes || []).map((lane) => ({{label: lane.name,
            value: `${{lane.kind}} | ${{lane.state}} | revision ${{lane.revision ?? "none"}} | ${{lane.head_digest ?? "no published head"}}`}}))));
        }} else if (activeTab === "plan") {{
          const plan = data.plan;
          if (plan) {{
            content.append(cards([{{label: "Plan revision", value: plan.revision ?? "none"}},
              {{label: "Page", value: plan.tasks.length ? `${{plan.offset + 1}}–${{plan.offset + plan.tasks.length}} of ${{plan.total_tasks}}${{plan.truncated ? " (more rows available)" : ""}}` : `No rows at offset ${{plan.offset}} (${{plan.total_tasks}} total)`}},
              ...plan.tasks.map((task) => ({{label: `${{task.position}}. ${{task.title}}`,
                value: `${{task.task_id}} | ${{task.state}} | dependencies: ${{task.dependencies.join(", ") || "none"}}`}}))]));
          }} else content.append(el("div", "empty", "No project Plan was requested."));
        }} else content.append(cards(data.facts));
        links.replaceChildren();
        const safeLinks = Array.isArray(data.links) ? data.links : [{{label: "Website", href: publicSite}}];
        safeLinks.forEach((item) => {{
          try {{
            const url = new URL(String(item.href || ""));
            if (url.origin !== publicSite || url.protocol !== "https:") return;
            const anchor = el("a", "", item.label || url.hostname);
            anchor.href = url.href;
            anchor.target = "_blank";
            anchor.rel = "noreferrer";
            links.append(anchor);
          }} catch (_) {{}}
        }});
      }}
      function selectTab(tab) {{
        activeTab = ["overview", "lanes", "plan"].includes(tab) ? tab : "overview";
        buttons.forEach((button) => button.setAttribute("aria-selected", String(button.dataset.tab === tab)));
        const openai = typeof window !== "undefined" ? window.openai : undefined;
        openai?.setWidgetState?.({{ activeTab }});
        render();
      }}
      function postToHost(message) {{
        if (window.parent !== window) window.parent.postMessage(message, "*");
      }}
      function hostTheme(context) {{
        if (["light", "dark"].includes(context?.theme)) document.documentElement.style.colorScheme = context.theme;
      }}
      function onHostMessage(event) {{
        if (event.source !== window.parent) return;
        const message = event.data;
        if (!message || message.jsonrpc !== "2.0") return;
        if (message.id === initializeRequestId) {{
          if (initialized || !Object.prototype.hasOwnProperty.call(message, "result")) return;
          if (message.result?.protocolVersion !== "2026-01-26") {{
            status.textContent = "UNSUPPORTED HOST";
            return;
          }}
          initialized = true;
          hostTheme(message.result.hostContext);
          postToHost({{
            jsonrpc: "2.0",
            method: "ui/notifications/initialized",
            params: {{}},
          }});
          return;
        }}
        if (initialized && message.method === "ui/notifications/tool-result") {{
          acceptSnapshot(message.params);
        }}
        if (initialized && message.method === "ui/notifications/host-context-changed") hostTheme(message.params);
        if (initialized && message.method === "ping" && message.id !== undefined) {{
          postToHost({{jsonrpc: "2.0", id: message.id, result: {{}}}});
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


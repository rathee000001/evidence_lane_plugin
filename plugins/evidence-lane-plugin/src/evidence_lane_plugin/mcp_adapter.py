"""Thin stdio MCP client adapter; all project execution stays in the engine."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
from pathlib import Path

from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, Resource, TextContent, Tool, ToolAnnotations

from . import __version__
from .connections import ConnectRequest, ProjectSelection
from .errors import LaneError
from .host_routing import HOST_MATRIX, ClientHello
from .launcher import verify_engine_binding
from .local_transport import LocalTransport
from .mcp_apps import (
    GOVERNED_PANEL_URI,
    MCP_APP_MIME_TYPE,
    governed_panel_html,
    governed_panel_resource_meta,
)
from .remote_transport import RemoteClientConfig, RemoteTransport
from .sdk import ActionResponse, EvidenceLaneClient


def tool_from_action(action: dict) -> Tool:
    arguments_schema = copy.deepcopy(action['inputSchema'])
    definitions = arguments_schema.pop('$defs', None)
    envelope = {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'project_id': {'type': ['string', 'null']},
            'expected_revision': {'type': ['integer', 'null'], 'minimum': 1},
            'arguments': arguments_schema,
        },
        'required': ['arguments'],
    }
    # Pydantic's local refs address the document root. Moving its schema below
    # `arguments` must preserve that root's definitions for nested contracts.
    if definitions is not None:
        envelope['$defs'] = definitions
    return Tool(
        name=action["name"], description=action["description"],
        inputSchema=envelope,
        outputSchema=ActionResponse.model_json_schema(),
        annotations=ToolAnnotations(
            readOnlyHint=not action["mutates"], destructiveHint=action["mutates"],
            openWorldHint=action["permission"] in {"network", "publish"},
        ),
        **({'_meta': {'ui': copy.deepcopy(action['ui'])}} if action.get('ui') else {}),
    )


async def serve(runtime_root: Path | None, *, host_profile: str = "unknown",
                remote_config: Path | None = None,
                project_selections: tuple[ProjectSelection, ...] = (), manage_projects: bool = False) -> None:
    if manage_projects and remote_config is not None:
        raise LaneError('LOCAL_PROJECT_ADMIN_ONLY', 'Remote clients cannot grant themselves owner project administration.')
    if project_selections and remote_config is not None:
        raise LaneError('LOCAL_SELECTION_ONLY', 'Remote access follows its configured OAuth grants, not local owner selections.')
    if len(project_selections) > 32 or len({item.project_id for item in project_selections}) != len(project_selections):
        raise LaneError('INVALID_SELECTION', 'Configure at most 32 distinct project selections.')
    server = Server("evidence-lane-plugin", version=__version__)
    transport: LocalTransport | RemoteTransport | None = None
    connect_lock = asyncio.Lock()

    async def connection() -> LocalTransport | RemoteTransport:
        nonlocal transport
        async with connect_lock:
            if transport is None:
                peer = server.request_context.session.client_params
                if peer is None:
                    raise LaneError("MCP_INITIALIZATION_REQUIRED", "Initialize the native MCP connection first.")
                hello = ClientHello(
                    configured_profile=host_profile, protocol="mcp_stdio",
                    peer_name=peer.clientInfo.name[:128], peer_version=peer.clientInfo.version[:128],
                    protocol_version=peer.protocolVersion[:32],
                    peer_capabilities=sorted(set(peer.capabilities.model_dump(exclude_none=True))
                                             & {"roots", "sampling", "elicitation", "tasks", "experimental"}),
                )
                if remote_config is not None:
                    configuration = await asyncio.to_thread(RemoteClientConfig.load, remote_config)
                    transport = await asyncio.to_thread(RemoteTransport, configuration, hello=hello)
                else:
                    if runtime_root is None:
                        raise LaneError('LOCAL_RUNTIME_ROOT_REQUIRED', 'Select the local engine runtime root before connecting.')
                    local = await asyncio.to_thread(LocalTransport, runtime_root,
                                                        connection=ConnectRequest(hello=hello, projects=list(project_selections), manage_projects=manage_projects))
                    try:
                        await asyncio.to_thread(verify_engine_binding, local)
                    except BaseException:
                        await asyncio.to_thread(local.close)
                        raise
                    transport = local
            return transport

    @server.list_tools()
    async def list_tools():
        selected = await connection()
        actions = await asyncio.to_thread(selected.catalog)
        return [tool_from_action(action) for action in actions]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            client = EvidenceLaneClient(await connection())
            response = await asyncio.to_thread(
                client.call, name, project_id=arguments.get("project_id"),
                expected_revision=arguments.get("expected_revision"),
                arguments=arguments.get("arguments", {}),
            )
            result = response.model_dump(mode="json")
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(result))],
                structuredContent=result, isError=response.status == "error",
            )
        except LaneError as error:
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps(error.public()))],
                isError=True,
            )

    async def panel_available():
        selected = await connection()
        actions = await asyncio.to_thread(selected.catalog)
        return any(action.get('ui', {}).get('resourceUri') == GOVERNED_PANEL_URI for action in actions)

    @server.list_resources()
    async def list_resources():
        if not await panel_available():
            return []
        return [Resource(uri=GOVERNED_PANEL_URI, name='Evidence Lane project console',
            description='Read-only runtime and selected-project views; static HTML contains no project data.',
            mimeType=MCP_APP_MIME_TYPE, _meta=governed_panel_resource_meta())]

    @server.read_resource()
    async def read_resource(uri):
        if str(uri) != GOVERNED_PANEL_URI or not await panel_available():
            raise ValueError('Unknown Evidence Lane UI resource')
        return [ReadResourceContents(content=await asyncio.to_thread(governed_panel_html),
            mime_type=MCP_APP_MIME_TYPE, meta=governed_panel_resource_meta())]

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())
    finally:
        if transport is not None:
            # Shutdown must finish the authenticated disconnect before stdio
            # teardown returns; cancellation of a helper task can otherwise
            # leave the server-side client live until expiry.
            transport.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Evidence Lane native stdio adapter")
    location = parser.add_mutually_exclusive_group(required=True)
    location.add_argument("--runtime-root", type=Path)
    location.add_argument("--remote-config", type=Path)
    parser.add_argument("--host-profile", choices=sorted(HOST_MATRIX), default="unknown")
    add_selection_arguments(parser)
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.runtime_root, host_profile=arguments.host_profile,
                      remote_config=arguments.remote_config, project_selections=configured_selections(arguments, parser),
                      manage_projects=arguments.manage_projects))


def add_selection_arguments(parser):
    from .projects import PERMISSIONS
    parser.add_argument('--manage-projects', action='store_true',
        help='Explicitly grant local-owner native project registration and selection; unavailable to remote clients.')
    parser.add_argument('--project-id', action='append', default=[], help='Explicit local owner-selected project; repeat for separate projects.')
    parser.add_argument('--permission', action='append', choices=sorted(PERMISSIONS), default=[],
                        help='Permission for these local selections; default read. Never inferred from an MCP tool call.')


def configured_selections(arguments, parser):
    from pydantic import ValidationError
    if (arguments.permission and not arguments.project_id) or (arguments.project_id and arguments.remote_config is not None):
        parser.error('Local project selections require --runtime-root and explicit project IDs.')
    try:
        values = tuple(ProjectSelection(project_id=item, permissions=sorted(set(arguments.permission or ['read'])))
                       for item in arguments.project_id)
    except ValidationError:
        parser.error('Project IDs must be canonical UUIDs.')
    if len(values) > 32 or len({item.project_id for item in values}) != len(values):
        parser.error('Configure at most 32 distinct projects.')
    return values


if __name__ == "__main__":
    main()

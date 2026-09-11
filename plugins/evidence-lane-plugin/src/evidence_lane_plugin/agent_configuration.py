"""Bounded instruction-chain and separate host/workspace recall inspection.

Retains global override and root-to-CWD discovery from the original resolver.
Observed file hashes are not a claim that Codex loaded that chain or changed
its native instructions. This workflow never writes instructions or memory.
"""
from __future__ import annotations

import hashlib
import os
import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from .errors import LaneError
from .registry import ActionSpec, Contract
from .storage import json_text, reject_links

PRIMARY_FILENAMES = ('AGENTS.override.md', 'AGENTS.md')
DEFAULT_PROJECT_DOC_MAX_BYTES = 32768
OFFICIAL_AGENTS_MD_GUIDE_URL = 'https://learn.chatgpt.com/docs/agent-configuration/agents-md'


class InstructionRequest(Contract):
    cwd_relative: str = Field(default='.', min_length=1, max_length=1024)
    include_global: bool = False
    include_workspace_recall: bool = True
    include_host_recall: bool = False
    max_bytes: int = Field(default=32768, ge=1024, le=262144)

    @field_validator('cwd_relative')
    @classmethod
    def contained_directory(cls, value):
        path = Path(value)
        if path.is_absolute() or path.drive or '..' in path.parts or any(c in value for c in '\r\n\x00'):
            raise ValueError('Choose a directory relative to the selected source root')
        return value


class InstructionSource(Contract):
    arm: Literal['instructions', 'workspace_recall', 'host_recall']
    scope: Literal['global', 'project']
    locator: str
    filename: str
    directory_depth: int
    sha256: str
    byte_count: int


class InstructionResult(Contract):
    project_id: str
    cwd_relative: str
    instructions: list[InstructionSource]
    workspace_recall: list[InstructionSource]
    host_recall: list[InstructionSource]
    inspected_config_digest: str | None
    instruction_chain_digest: str
    max_bytes: int
    consumed_bytes: int
    native_loaded_chain_attested: Literal[False] = False
    memory_truth_inferred: Literal[False] = False
    raw_text_returned: Literal[False] = False
    project_authorities_mutated: Literal[False] = False


def _read(path, *, root, limit):
    reject_links(path, Path(root.anchor))
    if not path.resolve().is_relative_to(root.resolve()):
        raise LaneError('INSTRUCTION_PATH_ESCAPE', 'The instruction locator escaped its selected root.')
    if not path.exists():
        return None
    try:
        with path.open('rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise LaneError('INSTRUCTION_BYTE_BUDGET', 'The applicable instruction or recall source exceeds the selected byte budget.')
        text = raw.decode('utf-8')
        if '\x00' in text:
            raise UnicodeError()
        return raw if text.strip() else None
    except (OSError, UnicodeError):
        raise LaneError('INSTRUCTION_SOURCE_UNREADABLE', 'An applicable source is not a readable UTF-8 file.') from None


def _configuration(codex_root):
    raw = _read(codex_root / 'config.toml', root=codex_root, limit=1048576)
    if raw is None:
        return [], DEFAULT_PROJECT_DOC_MAX_BYTES, None
    try:
        parsed = tomllib.loads(raw.decode('utf-8'))
        fallbacks = parsed.get('project_doc_fallback_filenames', [])
        maximum = parsed.get('project_doc_max_bytes', DEFAULT_PROJECT_DOC_MAX_BYTES)
        if (type(maximum) is not int or not 1 <= maximum <= 1048576 or not isinstance(fallbacks, list)
                or len(fallbacks) > 16 or any(not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name)
                                            or name in PRIMARY_FILENAMES for name in fallbacks)
                or len(set(fallbacks)) != len(fallbacks)):
            raise ValueError()
    except (ValueError, tomllib.TOMLDecodeError):
        raise LaneError('INSTRUCTION_CONFIG_INVALID', 'The selected Codex instruction discovery settings are invalid.') from None
    return fallbacks, maximum, hashlib.sha256(raw).hexdigest()


def resolve_agent_configuration(engine, context, request: InstructionRequest):
    client = engine.clients.session(context.client_id)
    engine.clients.context(client, context.project_id, 'read')
    if (request.include_global or request.include_host_recall) and not client.manage_projects:
        raise LaneError('HOST_RECALL_OWNER_SCOPE_REQUIRED', 'Global instruction and host recall inspection requires the local owner-granted administration channel.')
    project = engine.directory.open(context.project_id)
    root = project.source_root
    cwd = root / request.cwd_relative
    reject_links(cwd, root)
    if not cwd.is_dir() or not cwd.resolve().is_relative_to(root.resolve()):
        raise LaneError('INSTRUCTION_DIRECTORY_INVALID', 'Select an existing directory within this project source root.')
    relative = cwd.resolve().relative_to(root.resolve())
    if len(relative.parts) > 64:
        raise LaneError('INSTRUCTION_DIRECTORY_BUDGET', 'The selected instruction chain exceeds 64 directories.')
    directories, cursor = [root], root
    for part in relative.parts:
        cursor = cursor / part
        directories.append(cursor)
    codex_root = Path(os.environ.get('CODEX_HOME') or str(Path.home() / '.codex')).expanduser()
    if not codex_root.is_absolute():
        raise LaneError('INSTRUCTION_HOME_INVALID', 'The configured Codex home must be absolute.')
    fallbacks, maximum, config_digest = _configuration(codex_root) if request.include_global else ([], DEFAULT_PROJECT_DOC_MAX_BYTES, None)
    maximum = min(request.max_bytes, maximum)
    used = 0
    instructions: list[InstructionSource] = []
    workspace: list[InstructionSource] = []
    host: list[InstructionSource] = []

    def append(path, scope_root, scope, depth, arm, target):
        nonlocal used
        raw = _read(path, root=scope_root, limit=maximum - used)
        if raw is None:
            return False
        used += len(raw)
        target.append(InstructionSource(arm=arm, scope=scope, directory_depth=depth,
            locator=path.relative_to(scope_root).as_posix(), filename=path.name,
            sha256=hashlib.sha256(raw).hexdigest(), byte_count=len(raw)))
        return True

    if request.include_global:
        for filename in PRIMARY_FILENAMES:
            if append(codex_root / filename, codex_root, 'global', -1, 'instructions', instructions):
                break
    for depth, directory in enumerate(directories):
        for filename in (*PRIMARY_FILENAMES, *fallbacks):
            if append(directory / filename, root, 'project', depth, 'instructions', instructions):
                break
        if request.include_workspace_recall:
            append(directory / 'MEMORY.md', root, 'project', depth, 'workspace_recall', workspace)
    if request.include_host_recall:
        append(codex_root / 'memories/MEMORY.md', codex_root, 'global', -1, 'host_recall', host)
    return InstructionResult(project_id=project.project_id, cwd_relative=relative.as_posix(), instructions=instructions,
        workspace_recall=workspace, host_recall=host, inspected_config_digest=config_digest,
        instruction_chain_digest=hashlib.sha256(json_text([item.model_dump() for item in instructions]).encode()).hexdigest(),
        max_bytes=maximum, consumed_bytes=used)


def register_instruction_actions(engine):
    engine.registry.register(ActionSpec('instructions_inspect', 'Inspect scoped instruction and separate workspace/host recall file hashes; never import or modify their contents.',
        InstructionRequest, InstructionResult, lambda context, request: resolve_agent_configuration(engine, context, request),
        workflow='instructions'))

"""Supported native hook entrypoints over the persistent v4 capture channel.

No hook starts lifecycle work, grants projects, requests a continuation, reads
a transcript, changes the Goal or impersonates a Codex task.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import ValidationError

from .capture_routing import (
    HOOK_EVENT_ORDER,
    SUBAGENT_OBSERVER_EVENTS,
    HookEnvelope,
    normalize_hook,
    seal_observer_identity,
)
from .errors import LaneError
from .launcher import runtime_root, verify_engine_binding
from .local_transport import LocalTransport, owner_endpoint
from .redaction import redact
from .storage import json_text

MAX_HOOK_INPUT_BYTES = 262_144
HOOK_CONTRACT_SCHEMA = 'evidence-lane.native-hook-contract.v4'
HOOK_EVENT_NAMES = HOOK_EVENT_ORDER
HOOK_COMMON_FIELDS = frozenset({'hook_event_name', 'session_id', 'turn_id', 'model'})
HOOK_EVENT_FIELDS = {
    'SessionStart': {'source'},
    'SubagentStart': {'agent_id', 'agent_type'},
    'SubagentStop': {'agent_id', 'agent_type'},
    'SessionEnd': {'reason'},
    'UserPromptSubmit': {'prompt'},
    'PreToolUse': {'tool_name', 'tool_use_id', 'tool_input'},
    'PermissionRequest': {'tool_name', 'tool_input'},
    'PostToolUse': {'tool_name', 'tool_use_id', 'tool_input', 'tool_response'},
    'PreCompact': {'trigger'},
    'PostCompact': {'trigger'},
    'Stop': {'last_assistant_message', 'stop_hook_active'},
    'Interrupt': set(),
}
HOOK_PIPELINE = (
    {'id': 'read_bounded_input', 'owner': 'event_handler',
     'implementation': 'hook_contract.main_for_event', 'max_bytes': MAX_HOOK_INPUT_BYTES},
    {'id': 'validate_and_redact_visible_fields', 'owner': 'shared_hook_contract',
     'implementation': 'hook_contract.prepare_hook'},
    {'id': 'verify_bound_engine_build', 'owner': 'shared_hook_contract',
     'implementation': 'hook_contract.submit_hook and launcher.verify_engine_binding'},
    {'id': 'deliver_once_to_bound_project', 'owner': 'shared_hook_contract',
     'implementation': 'hook_contract.submit_hook', 'automatic_retry': False},
    {'id': 'verify_receipt_and_emit_bounded_output', 'owner': 'shared_hook_contract',
     'implementation': 'hook_contract.context_hook_output'},
)


def hook_event_handler_path(name: str) -> str:
    if name not in HOOK_EVENT_NAMES:
        raise LaneError('HOOK_EVENT_UNSUPPORTED', 'Select a documented packaged hook event.')
    return f'hooks/events/{name}/handler.py'


def hook_event_input_schema(name: str) -> dict:
    """Describe the documented input that the event-owned handler admits.

    Additional host fields are allowed because the executable selects only the
    visible allowlist before redaction and persistence.
    """
    if name not in HOOK_EVENT_NAMES:
        raise LaneError('HOOK_EVENT_UNSUPPORTED', 'Select a documented packaged hook event.')
    properties: dict[str, dict] = {
        'hook_event_name': {'const': name},
        'session_id': {'type': 'string', 'minLength': 1, 'maxLength': 128},
        'turn_id': {'type': ['string', 'null'], 'maxLength': 128},
        'model': {'type': ['string', 'null'], 'maxLength': 200},
    }
    required = ['hook_event_name', 'session_id']
    for field in sorted(HOOK_EVENT_FIELDS[name]):
        if field in {'tool_input', 'tool_response'}:
            properties[field] = {}
        elif field == 'stop_hook_active':
            properties[field] = {'type': 'boolean'}
        else:
            properties[field] = {'type': ['string', 'null']}
    for field in {
        'UserPromptSubmit': {'prompt'},
        'PreToolUse': {'tool_name', 'tool_use_id'},
        'PermissionRequest': {'tool_name'},
        'PostToolUse': {'tool_name', 'tool_use_id'},
        'SubagentStart': {'agent_id', 'agent_type'},
        'SubagentStop': {'agent_id', 'agent_type'},
    }.get(name, set()):
        required.append(field)
        properties[field] = {'type': 'string', 'minLength': 1}
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        '$id': f'evidence-lane://hooks/{name}/input/v4',
        'title': f'Evidence Lane {name} visible input',
        'type': 'object',
        'properties': properties,
        'required': sorted(required),
        'additionalProperties': True,
        'x-evidence-lane-captured-fields': sorted(HOOK_COMMON_FIELDS | HOOK_EVENT_FIELDS[name]),
        'x-native-task-attestation': 'not_provided',
    }


def hook_event_contract(name: str) -> dict:
    if name not in HOOK_EVENT_NAMES:
        raise LaneError('HOOK_EVENT_UNSUPPORTED', 'Select a documented packaged hook event.')
    return {
        'schema': 'evidence-lane.native-hook-event.v4',
        'event': name,
        'order': HOOK_EVENT_NAMES.index(name) + 1,
        'entrypoint': hook_event_handler_path(name),
        'input_schema': f'hooks/events/{name}/event.schema.json',
        'pipeline': f'hooks/events/{name}/pipeline.v4.json',
        'execution_owner': 'evidence_lane_plugin.hook_contract',
        'route': '/v4/capture',
        'remote_route': '/remote/v4/capture',
        'requires_explicit_project_session_binding': True,
        'automatic_retry': False,
        'bounded_context_output': name in {'SessionStart', 'UserPromptSubmit'},
        'host_control_output': False,
        'subagent_control_output': False,
        'native_installation_verified': False,
    }


def prepare_hook(raw: bytes, expected_event: str) -> HookEnvelope:
    if len(raw) > MAX_HOOK_INPUT_BYTES:
        raise LaneError('HOOK_INPUT_BUDGET', 'The native hook payload exceeds its capture budget.')
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get('hook_event_name') != expected_event:
            raise ValueError()
        allowed = HOOK_COMMON_FIELDS | HOOK_EVENT_FIELDS[expected_event]
        selected = {key: value[key] for key in allowed if key in value}
        if expected_event in SUBAGENT_OBSERVER_EVENTS:
            selected = seal_observer_identity(selected)
        event = redact(selected)
        envelope = HookEnvelope(event_id=str(uuid4()), event=event)
        normalize_hook(envelope)
        return envelope
    except (ValueError, KeyError, TypeError, ValidationError, RecursionError):
        raise LaneError('HOOK_INPUT_INVALID', 'The native hook input does not match this event contract.') from None


def submit_hook(envelope: HookEnvelope, selected_root: Path | None = None) -> dict:
    """One attributed delivery attempt; an uncertain response is never replayed."""
    remote_config = os.environ.get('EVIDENCE_LANE_REMOTE_CONFIG')
    if remote_config:
        from .remote_transport import RemoteClientConfig, submit_remote_hook
        return submit_remote_hook(RemoteClientConfig.load(Path(remote_config)), envelope)
    root = runtime_root(selected_root)
    timeout = 0.75 if envelope.event['hook_event_name'] in {'Interrupt', 'SessionEnd'} else 3
    # Native task identity remains unavailable. This verifies the selected
    # executable source, while the engine checks its explicit capture binding.
    with LocalTransport(root, timeout=timeout) as transport:
        health = verify_engine_binding(transport)
    record, credential = owner_endpoint(root)
    if record['instance_id'] != health['instance_id']:
        raise LaneError('ENGINE_INSTANCE_CHANGED', 'The engine changed before hook delivery.')
    payload = {'instance_id': record['instance_id'], 'capture': envelope.model_dump(mode='json')}
    try:
        with (httpx.Client(timeout=timeout, trust_env=False, follow_redirects=False) as client,
              client.stream('POST', f"http://127.0.0.1:{record['port']}/v4/capture",
                  headers={'Authorization': 'Bearer ' + credential}, json=payload) as response):
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > 65_536:
                    raise ValueError()
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise TypeError()
            if response.status_code == 409 and result.get('error') == 'CAPTURE_BINDING_REQUIRED':
                return {'captured': False, 'reason': 'project_session_not_bound'}
            if response.status_code != 200 or result.get('event_id') != envelope.event_id:
                raise ValueError()
            return {'captured': True, 'result': result, 'engine_instance_id': record['instance_id']}
    except (httpx.HTTPError, ValueError, TypeError, RecursionError):
        raise LaneError('HOOK_DELIVERY_UNCONFIRMED', 'Native hook capture was not confirmed; no automatic retry was performed.') from None


def hook_manifest() -> dict:
    hooks = {}
    for name in HOOK_EVENT_NAMES:
        hooks[name] = [{'hooks': [{
            'type': 'command',
            'command': 'python3 -I -B "${PLUGIN_ROOT}/' + hook_event_handler_path(name) + '"',
            'commandWindows': '& python -I -B "${PLUGIN_ROOT}\\' + hook_event_handler_path(name).replace('/', '\\') + '"',
            'timeout': 3 if name in {'Interrupt', 'SessionEnd'} else 10,
            'statusMessage': 'Record bound Evidence Lane ' + name + ' evidence',
        }]}]
        if name in {'SessionStart', 'UserPromptSubmit'}:
            hooks[name][0]['hooks'][0]['additionalContextLimit'] = 3000
    return {'description': 'Supported v4 visible-event capture through the plugin-owned engine; no lifecycle or host-control instructions.', 'hooks': hooks}


def hook_registry() -> dict:
    return {'schema': HOOK_CONTRACT_SCHEMA, 'source': 'capture_routing.HOOK_EVENT_ORDER',
        'documentation': 'https://learn.chatgpt.com/docs/hooks',
        'events': [{'name': name, 'order': index, 'input_fields': sorted(HOOK_COMMON_FIELDS | HOOK_EVENT_FIELDS[name]),
            'handler': hook_event_handler_path(name), 'shared_implementation': 'src/evidence_lane_plugin/hook_contract.py',
            'input_schema': f'hooks/events/{name}/event.schema.json',
            'event_contract': f'hooks/events/{name}/event.v4.json',
            'pipeline_contract': f'hooks/events/{name}/pipeline.v4.json',
            'route': '/v4/capture', 'requires_explicit_project_session_binding': True,
            'remote_route': '/remote/v4/capture', 'remote_config_env': 'EVIDENCE_LANE_REMOTE_CONFIG',
            'host_control_output': False, 'bounded_context_output': name in {'SessionStart', 'UserPromptSubmit'},
            'automatic_retry': False,
            **({'observer_identity_transport': 'bounded_agent_id_sha256_and_reported_type',
                'observer_payload_retention': 'metadata_only', 'subagent_control_output': False}
               if name in SUBAGENT_OBSERVER_EVENTS else {})} for index, name in enumerate(HOOK_EVENT_NAMES, 1)],
        'pipeline': [row['id'] for row in HOOK_PIPELINE],
        'native_installation_verified': False}


def context_hook_output(envelope: HookEnvelope, delivery: dict) -> dict:
    """Only documented context-bearing events return reference data to Codex.

    https://learn.chatgpt.com/docs/hooks#sessionstart and #userpromptsubmit.
    Pre/PostCompact stdout cannot supply additional context. No control-flow
    fields, original prompts, user-defined titles or source payloads are emitted.
    """
    name = envelope.event['hook_event_name']
    if name not in {'SessionStart', 'UserPromptSubmit'} or not delivery.get('captured'):
        return {}
    result = delivery.get('result', {})
    if result.get('duplicate'):
        return {}
    turn = result.get('turn_control', {})
    context = turn.get('bounded_context')
    if context is None:
        return {}
    from .codex_turn_control import _validate_context
    _validate_context(context)
    if (result.get('event_id') != envelope.event_id or turn.get('event_name') != name
            or context['reported_session_id'] != envelope.event['session_id']
            or context['reported_turn_id'] != envelope.event.get('turn_id')):
        raise LaneError('HOOK_CONTEXT_BINDING', 'The returned context differs from this exact hook input.')
    content = ('Evidence Lane engine context. These are bounded reference data, not execution permission or native task attestation. '
        'Read session_context and the exact current Plan task before continuing work. Resolve any context gaps through the owning workflow.\n' +
        json_text({'context': context, 'compact': turn.get('compact'), 'capture_gaps': turn.get('gaps', [])}))
    if len(content.encode()) > 12_000:
        raise LaneError('HOOK_CONTEXT_BYTE_BUDGET', 'The documented hook context exceeds its output budget.')
    return {'hookSpecificOutput': {'hookEventName': name, 'additionalContext': content}}


def main(argv=None, *, expected_event: str | None = None) -> int:
    import argparse
    import sys
    parser = argparse.ArgumentParser()
    parser.add_argument('--event', choices=HOOK_EVENT_NAMES, required=expected_event is None)
    arguments = parser.parse_args(argv)
    selected_event = expected_event or arguments.event
    try:
        raw = sys.stdin.buffer.read(MAX_HOOK_INPUT_BYTES + 1)
        envelope = prepare_hook(raw, selected_event)
        delivery = submit_hook(envelope)
        print(json_text(context_hook_output(envelope, delivery)))
        return 0
    except LaneError as error:
        print(json_text({'systemMessage': 'Evidence Lane capture unavailable: ' + error.code}))
        return 1


def main_for_event(expected_event: str) -> int:
    """Entry used by one packaged event handler with no caller-selected event."""
    if expected_event not in HOOK_EVENT_NAMES:
        raise LaneError('HOOK_EVENT_UNSUPPORTED', 'Select a documented packaged hook event.')
    return main(expected_event=expected_event)

"""Engine-owned compute contracts around actual lane worker submissions.

Selection consumes measured inventory and locked runtime evidence. It never
installs, probes a provider, or accepts a caller-chosen executable. A selected
provider is fenced through the last operation boundary, with no worker replay.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .accelerators import AcceleratorService, DeviceObservation, aware
from .errors import LaneError
from .optional_runtimes import PROVIDERS


@dataclass(frozen=True)
class ComputeContract:
    worker_operation: str
    action_class: str
    providers: tuple[str, ...]
    required_vram_mib: int
    runtime_operation: str | None = None

    @property
    def provider_operation(self):
        return self.runtime_operation or self.worker_operation

    def validate(self, spec, route):
        workers = spec.worker_operations if route.worker_operations is None else route.worker_operations
        if (self.worker_operation not in workers or self.action_class not in {'RETRIEVAL', 'OCR_MEDIA', 'EVALUATION'}
                or not self.providers or self.providers[0] != 'CPU' or len(set(self.providers)) != len(self.providers)
                or not set(self.providers) <= {'CPU', *PROVIDERS.values()}
                or not self.provider_operation.isidentifier() or len(self.provider_operation) > 100
                or type(self.required_vram_mib) is not int or not 1 <= self.required_vram_mib <= 1_048_576):
            raise LaneError('COMPUTE_CONTRACT_INVALID', 'Register the actual worker, supported providers and bounded memory requirement.')

    def schema(self):
        return asdict(self) | {'providers': list(self.providers), 'selection_is_execution': False,
            'fallback_policy': 'before_first_invocation_only', 'memory_basis': 'adapter_conservative_reservation'}


def binding(selection):
    """Stable admission identity excludes observation time and random probe IDs."""
    return {key: selection.get(key) for key in ('settings_revision', 'settings_digest', 'selected_provider',
        'device_id', 'device_index', 'environment_digest', 'runtime_id', 'runtime_manifest_sha256', 'provider_worker_id')}


class ComputeRouter:
    def __init__(self, engine):
        self.engine = engine

    def select(self, contract, context):
        service = AcceleratorService(self.engine.directory.open(context.project_id))
        settings = service.settings()
        default = {'settings_revision': settings['revision'], 'settings_digest': settings.get('digest'),
            'selected_provider': 'CPU', 'device_id': None, 'device_index': None,
            'environment_digest': None, 'runtime_id': None, 'runtime_manifest_sha256': None,
            'provider_worker_id': None,
            'execution_state': 'not_executed', 'fallback_reasons': [], 'contract': contract.schema()}
        if 'tools' not in context.permissions:
            return default | {'fallback_reasons': ['COMPUTE_TOOLS_GRANT_REQUIRED']}
        config = settings.get('config')
        admitted = (context.tool_admission or {}).get('compute')
        if admitted and admitted.get('selected_provider') == 'CPU':
            # Completion of background GPU warmup must not switch or cancel an
            # already admitted CPU operation. Settings changes remain fenced.
            return default | {'fallback_reasons': ['ADMITTED_CPU_PROVIDER_RETAINED']}
        inventory = (self.engine.capabilities.compute_inventory() if config and config['requested_profile'] != 'cpu'
                     else self.engine.capabilities.snapshot())
        devices = [DeviceObservation.model_validate(row) for row in inventory.get('devices', [])]
        workers = getattr(self.engine.capabilities, 'provider_workers', None)
        preparation_reasons = []
        if workers is not None and config and config['requested_profile'] != 'cpu':
            service._authorize(context, 'tools')
            candidates = [device for device in devices if device.vendor in config['enabled_vendor_plugins']
                and config['requested_profile'] in {'auto', device.vendor}
                and (config['device_id'] is None or config['device_id'] == device.device_id)]
            unexpired = config['expires_at'] == 'NO_EXPIRY' or aware(config['expires_at']) > service.clock()
            if len(candidates) == 1 and unexpired and contract.action_class in config['action_classes']:
                device = candidates[0]
                if (device.total_vram_mib and device.used_vram_mib is not None and device.temperature_c is not None
                        and device.used_vram_mib + contract.required_vram_mib <= device.total_vram_mib * config['memory_budget_percent'] / 100
                        and device.temperature_c <= config['temperature_limit_c'] and device.throttle_active is not True
                        and (device.throttle_active is not None or device.source == 'amd_adlx')
                        and 0 <= (service.clock() - aware(device.observed_at)).total_seconds() <= 10):
                    for runtime in self.engine.capabilities.runtimes.values():
                        provider = PROVIDERS.get(runtime.runtime_id)
                        runtime_vendor = (
                            'nvidia' if runtime.runtime_id == 'cuda'
                            else 'amd' if runtime.runtime_id == 'rocm'
                            else device.vendor
                        )
                        if provider in contract.providers and runtime_vendor == device.vendor:
                            try:
                                if runtime.supports_operation(contract.provider_operation):
                                    phase = workers.prepare(runtime, device, contract)
                                    if phase != 'ready':
                                        preparation_reasons.append('PROVIDER_OPERATION_' + phase.upper())
                            except (LaneError, OSError, ValueError, KeyError):
                                pass
        runtimes, unsupported = [], preparation_reasons
        for probe in inventory.get('provider_probes', []):
            runtime = self.engine.capabilities.runtimes.get(probe.get('runtime_id'))
            if runtime is None or PROVIDERS.get(runtime.runtime_id) not in contract.providers:
                continue
            try:
                supported = runtime.supports_operation(contract.provider_operation)
            except (LaneError, OSError, ValueError, KeyError):
                unsupported.append('RUNTIME_BINDING_INVALID')
                continue
            if not supported:
                unsupported.append('RUNTIME_OPERATION_NOT_INSTALLED')
                continue
            if workers is not None and workers.identity(runtime.runtime_id, probe.get('device_id')) is None:
                unsupported.append('PROVIDER_OPERATION_PREWARMING_OR_UNAVAILABLE')
                continue
            evidence = runtime.accelerator_evidence(probe)
            if evidence is not None:
                runtimes.append(evidence)
        selected = service.select(context=context, action_class=contract.action_class, devices=devices,
            runtimes=runtimes, required_vram_mib=contract.required_vram_mib,
            expected_revision=settings['revision'])
        selected = default | selected
        if selected['selected_provider'] == 'CPU':
            requested_provider = config.get('requested_profile') if config else 'cpu'
            if requested_provider != 'cpu' and any(PROVIDERS.get(row.get('runtime_id')) not in contract.providers
                                                 for row in inventory.get('provider_probes', [])):
                unsupported.append('OPERATION_PROVIDER_UNSUPPORTED')
            selected['fallback_reasons'] = list(dict.fromkeys(unsupported + selected['fallback_reasons']))
            return selected
        runtime_id = next(key for key, value in PROVIDERS.items() if value == selected['selected_provider'])
        runtime = self.engine.capabilities.runtimes[runtime_id]
        device = next(row for row in devices if row.device_id == selected['device_id'])
        return selected | {'runtime_id': runtime_id, 'runtime_manifest_sha256': runtime.manifest_sha256,
            'provider_worker_id': workers.identity(runtime_id, device.device_id) if workers is not None else None,
            'device_index': selected['runtime_device_index'] if selected.get('runtime_device_index') is not None else device.device_index}

    def invocation(self, contract, context, selection):
        return ComputeInvocation(self, contract, context, selection)


class ComputeInvocation:
    def __init__(self, router, contract, context, selection):
        self.router, self.contract, self.context = router, contract, context
        self.selection = selection
        self.expected = binding(selection)

    def check(self):
        if self.context.authorize is not None:
            self.context.authorize('read')
            if self.expected['selected_provider'] != 'CPU':
                self.context.authorize('tools')
        current = self.router.select(self.contract, self.context)
        actual = binding(current)
        keys = ('settings_revision', 'settings_digest') if self.expected['selected_provider'] == 'CPU' else self.expected
        if any(actual[key] != self.expected[key] for key in keys):
            raise LaneError('COMPUTE_ADMISSION_CHANGED', 'The compute grant, device, budget or runtime changed; no automatic replay was issued.')

    def arguments(self, operation, arguments):
        if operation != self.contract.worker_operation or '_compute' in arguments:
            raise LaneError('COMPUTE_WORKER_SCOPE', 'Compute inputs belong to the declared engine worker only.')
        self.check()
        provider = self.expected['selected_provider']
        payload = {'selected_provider': provider}
        if provider != 'CPU':
            runtime = self.router.engine.capabilities.runtimes[self.expected['runtime_id']]
            payload |= runtime.worker_binding() | {'device_id': self.expected['device_id'],
                'device_index': self.expected['device_index'], 'required_vram_mib': self.contract.required_vram_mib}
            if self.expected['provider_worker_id'] is not None:
                payload['provider_worker'] = self.router.engine.capabilities.provider_workers.binding(
                    self.expected['runtime_id'], self.expected['device_id'], self.expected['provider_worker_id'])
        return dict(arguments) | {'_compute': payload}

    def submit(self, operation, arguments):
        prepared = self.arguments(operation, arguments)
        future = (self.context.execution.submit(operation, prepared) if self.context.execution is not None
            else self.router.engine.workers.submit(operation, prepared))
        if self.expected['provider_worker_id'] is not None:
            self.router.engine.capabilities.provider_workers.track(future, self.selection)
        return future

    def result(self, response):
        # A completed provider attempt is never retried on another provider.
        self.check()
        if response.get('status') != 'ok':
            raise LaneError('COMPUTE_WORKER_FAILED', 'The selected compute worker failed; no automatic replay was issued.')
        evidence = response.get('result', {}).get('compute', {})
        # Native text can satisfy every selected PDF page. This is an explicit
        # no-model path, never evidence that the selected GPU executed OCR.
        value = response.get('result', {})
        no_ocr = (self.contract.worker_operation == 'pdf_ocr' and bool(value.get('pages'))
            and all(row.get('ocr_selected') is False and row.get('ocr_lines') == 0 for row in value['pages'])
            and value.get('lines') == [] and value.get('evidence', {}).get('invoked') is False
            and evidence == {'selected_provider': None, 'execution_state': 'not_required',
                'reason': 'native_text_satisfied_selected_pages'})
        if no_ocr:
            return evidence
        if (evidence.get('selected_provider') != self.expected['selected_provider']
                or evidence.get('execution_state') != 'executed'
                or (self.expected['selected_provider'] != 'CPU' and any(evidence.get(key) != self.expected[key]
                    for key in ('device_id', 'device_index', 'environment_digest', 'runtime_id', 'runtime_manifest_sha256', 'provider_worker_id')))):
            raise LaneError('COMPUTE_RESULT_UNBOUND', 'The worker result does not prove the selected compute binding.')
        return evidence

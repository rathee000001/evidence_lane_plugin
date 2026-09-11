"""Shared stateless OCR adapter; PDF and media keep separate lane results."""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from .errors import LaneError
from .optional_runtimes import OptionalRuntime
from .shared_tool_assets import resolve_shared_asset


class ProviderOcr:
    def __init__(self, language, selection):
        if language != 'eng' or selection.get('selected_provider') != 'DIRECTML':
            raise LaneError('OCR_PROVIDER_UNAVAILABLE', 'The admitted provider does not support this OCR operation.')
        self.runtime = OptionalRuntime.from_worker_binding(selection)
        if self.runtime.runtime_id != 'directml' or not self.runtime.supports_operation('rapidocr_lines'):
            raise LaneError('OCR_PROVIDER_UNAVAILABLE', 'The admitted OCR environment is unavailable.')
        self.selection = selection
        self.folder, self.asset = resolve_shared_asset('rapidocr_models')
        self.evidence = {'engine': 'RapidOCR', 'models_sha256': self.asset['files_sha256'],
            'models_downloaded': False, 'provider': 'DmlExecutionProvider', 'acceleration_claimed': False}

    def lines(self, png):
        if not 1 <= len(png) <= 100_663_296:
            raise LaneError('OCR_RASTER_BUDGET', 'The rendered OCR input exceeds its byte budget.')
        with tempfile.TemporaryDirectory(prefix='evidence-lane-ocr-input-') as temporary:
            path = Path(temporary) / 'raster.png'
            path.write_bytes(png)
            value = self.runtime.execute('rapidocr_lines', {'language': 'eng',
                'model_path': str(self.folder), 'model_files': self.asset['files'],
                'asset_identity': self.asset['files_sha256'], 'png_path': str(path),
                'png_sha256': hashlib.sha256(png).hexdigest(), 'png_bytes': len(png)},
                device_id=self.selection['device_id'], device_index=self.selection['device_index'],
                required_vram_mib=self.selection['required_vram_mib'], server_binding=self.selection.get('provider_worker'))
        if (value.get('raster_sha256') != hashlib.sha256(png).hexdigest()
                or value.get('asset_identity') != self.asset['files_sha256']
                or resolve_shared_asset('rapidocr_models')[1]['files_sha256'] != self.asset['files_sha256']):
            raise LaneError('OCR_PROVIDER_BINDING', 'OCR source or model bindings changed.')
        self.evidence.update(value['evidence'], compute=value['compute'])
        return value['lines']

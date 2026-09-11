"""A selected OCR dependency cannot admit overlapping cv2 distributions."""
import importlib.metadata

import pytest
from evidence_lane_plugin.registry import ActionContext
from evidence_lane_plugin.tool_routes import ToolRouter


@pytest.mark.parametrize('names,expected,reason', [
    (['opencv-python', 'opencv-python-headless'], False, 'OPENCV_DISTRIBUTION_CONFLICT'),
    (['opencv-python', 'opencv-contrib-python'], False, 'OPENCV_DISTRIBUTION_CONFLICT'),
    (['opencv-python-headless', 'opencv-contrib-python-headless'], False, 'OPENCV_DISTRIBUTION_CONFLICT'),
    (['opencv-python-headless'], False, 'OPENCV_DISTRIBUTION_UNSUPPORTED'),
    ([], False, 'DEPENDENCY_UNAVAILABLE'),
    (['opencv-python'], True, 'PACKAGE_PRESENT'),
])
def test_ocr_preflight_requires_one_compatible_cv2_distribution(monkeypatch, names, expected, reason):
    original = importlib.metadata.version

    def version(name):
        if name.startswith('opencv-'):
            if name in names:
                return '5.0.0.93'
            raise importlib.metadata.PackageNotFoundError(name)
        return original(name)

    monkeypatch.setattr(importlib.metadata, 'version', version)
    context = ActionContext('fixture', None, frozenset({'read'}))
    observed = ToolRouter.observe('OpenCV', context)
    assert observed['ready'] is expected
    assert observed['reason'] == reason
    assert not observed['tool_executed']


def test_catalog_exposes_conflict_without_reporting_a_ready_package(monkeypatch):
    from evidence_lane_plugin.tool_catalog import snapshot

    original = importlib.metadata.version

    def version(name):
        if name in {'opencv-python', 'opencv-python-headless'}:
            return '5.0.0.93'
        if name.startswith('opencv-'):
            raise importlib.metadata.PackageNotFoundError(name)
        return original(name)

    monkeypatch.setattr(importlib.metadata, 'version', version)
    opencv = next(row for row in snapshot({'tools': []})['entries'] if row['tool_id'] == 'OpenCV')
    assert opencv['readiness'] == 'package_conflict'
    assert opencv['availability_reason'] == 'OPENCV_DISTRIBUTION_CONFLICT'
    assert opencv['execution_state'] == 'not_verified'

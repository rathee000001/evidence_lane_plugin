from __future__ import annotations

import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN))
sys.path.insert(0, str(PLUGIN / "src"))


def test_all_sdk_families_import_the_current_executable_owners():
    from sdk.actions import ActionRequest, EvidenceLaneClient
    from sdk.authorities import authority_package_folder
    from sdk.delta import DeltaService
    from sdk.env_uop import load_env_uop_runtime_authority
    from sdk.hooks import HOOK_EVENT_NAMES
    from sdk.host import HostDetector
    from sdk.internal import ActionRegistry
    from sdk.plan import HostPlanBind, PlanStore
    from sdk.recovery import DatabaseRecovery
    from sdk.routing import current_implementation_registry
    from sdk.skills import WORKFLOWS
    from sdk.transports import LocalTransport
    from sdk.workflows import WorkflowCatalog

    assert ActionRequest and EvidenceLaneClient and DeltaService and HostDetector
    assert ActionRegistry and HostPlanBind and PlanStore and DatabaseRecovery
    assert LocalTransport and WorkflowCatalog and load_env_uop_runtime_authority
    assert current_implementation_registry and authority_package_folder
    assert len(HOOK_EVENT_NAMES) == 12 and len(WORKFLOWS) == 24

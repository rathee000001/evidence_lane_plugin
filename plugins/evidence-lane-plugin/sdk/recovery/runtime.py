"""Bind SDK recovery to coherent separate-lane backup and restore owners."""

from evidence_lane_plugin.database_recovery import (
    DatabaseRecovery,
    close_recovery_session,
    restore_backup_offline,
    verify_backup,
    verify_recovery_history,
)

from ..contracts import load_sdk_contract


def recovery_contract_catalog():
    """Return coherent database, job and Git recovery contracts."""

    return load_sdk_contract("recovery/recovery-contract-registry.v4.json")

__all__ = [
    "DatabaseRecovery",
    "close_recovery_session",
    "recovery_contract_catalog",
    "restore_backup_offline",
    "verify_backup",
    "verify_recovery_history",
]

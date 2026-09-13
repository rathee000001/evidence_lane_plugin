"""Sessions: exact Boot, Resume, locked Flash and host binding authority."""
from evidence_lane_plugin.authority_support import (
    refresh_authority_support,
    validate_authority_support,
)
from evidence_lane_plugin.session_authority import SESSION_MIGRATIONS, SessionAuthority

AUTHORITY_ID = 'sessions'


def initialize(project, *, writer):
    return refresh_authority_support(project, AUTHORITY_ID, writer=writer)


def inspect(project):
    return validate_authority_support(project, AUTHORITY_ID)


__all__ = ['AUTHORITY_ID', 'SESSION_MIGRATIONS', 'SessionAuthority', 'initialize', 'inspect']

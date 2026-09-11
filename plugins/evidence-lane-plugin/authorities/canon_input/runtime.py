"""canon: the actual owner implementation and separate project store."""
from evidence_lane_plugin.authority_support import (
    canonical_authority_module,
    refresh_authority_support,
    validate_authority_support,
)

AUTHORITY_ID = 'canon'


def canonical_module():
    return canonical_authority_module(AUTHORITY_ID)


def initialize(project, *, writer):
    return refresh_authority_support(project, AUTHORITY_ID, writer=writer)


def inspect(project):
    return validate_authority_support(project, AUTHORITY_ID)

"""Lane-specific binding to the canonical shared lane bundle builder."""

from evidence_lane_plugin.lane_engine import build_lane_bundle

LANE_ID = "brain_loader"

def build_lane_sources(*, source_paths, source_overrides=None, **kwargs):
    paths = tuple(dict.fromkeys(str(path) for path in source_paths))
    overrides = dict(source_overrides or {})
    overrides.update({path: LANE_ID for path in paths})
    return build_lane_bundle(
        source_paths_override=paths,
        source_overrides=overrides,
        **kwargs,
    )

__all__ = ["LANE_ID", "build_lane_sources"]

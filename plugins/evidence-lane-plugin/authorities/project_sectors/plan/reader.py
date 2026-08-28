"""Lane-specific binding to live pointer/MMD/DOT/SQLite traversal."""

from evidence_lane_plugin.lane_reader import LaneReader

LANE_ID = "plan"

def lane_status(reader: LaneReader, project_id: str):
    return reader.lane_status(project_id, LANE_ID)

def search(reader: LaneReader, project_id: str, query: str, **kwargs):
    return reader.search(project_id, LANE_ID, query, **kwargs)

__all__ = ["LANE_ID", "lane_status", "search"]

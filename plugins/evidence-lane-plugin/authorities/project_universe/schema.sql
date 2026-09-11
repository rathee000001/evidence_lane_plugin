-- Projection: runtime applies the real ordered migrations under its project writer.

-- universe v1, digest 738098e3bde2ea457809e5735318c13863466e2433789f8c8da5390ea49e4617
CREATE TABLE universe_links (
        target_project_id TEXT PRIMARY KEY, version INTEGER NOT NULL CHECK(version>0),
        state TEXT NOT NULL CHECK(state IN ('linked','unlinked')),
        binding_digest TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)));
CREATE TABLE universe_link_events (
        sequence INTEGER PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
        actor_id TEXT NOT NULL, input_digest TEXT NOT NULL,
        body_json TEXT NOT NULL CHECK(json_valid(body_json)),
        previous_digest TEXT, digest TEXT NOT NULL UNIQUE);
-- federation v1, digest 25bc500aed38d1cbdf7bbdaa841024564b81e9f9fe54b07fc926f7646092c53b
CREATE TABLE federation_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        federation_id TEXT NOT NULL UNIQUE, body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL);
CREATE TABLE federation_members (project_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL);
CREATE TABLE federation_member_history (project_id TEXT NOT NULL, version INTEGER NOT NULL,
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL, PRIMARY KEY(project_id,version));
CREATE TABLE federation_mini_brains (mini_brain_id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
        lane_id TEXT NOT NULL, body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL);
CREATE INDEX federation_mini_project ON federation_mini_brains(project_id,lane_id,mini_brain_id);
CREATE TABLE federation_lane_heads (project_id TEXT NOT NULL, lane_id TEXT NOT NULL,
        mini_brain_id TEXT NOT NULL REFERENCES federation_mini_brains(mini_brain_id), PRIMARY KEY(project_id,lane_id));
CREATE TABLE federation_grants (grant_id TEXT PRIMARY KEY,
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL);
CREATE TABLE federation_revocations (grant_id TEXT PRIMARY KEY REFERENCES federation_grants(grant_id),
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL);
CREATE TABLE federation_links (edge_id TEXT PRIMARY KEY, grant_id TEXT NOT NULL REFERENCES federation_grants(grant_id),
        body_json TEXT NOT NULL CHECK(json_valid(body_json)), digest TEXT NOT NULL);

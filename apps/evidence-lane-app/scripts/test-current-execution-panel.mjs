import crypto from "node:crypto";
import fs from "node:fs";

const snapshot = JSON.parse(
  fs.readFileSync(new URL("../app/_data/website-plan-projection.json", import.meta.url), "utf8"),
);
const publicMetadata = JSON.parse(
  fs.readFileSync(new URL("../public/.well-known/evidence-lane-plugin.json", import.meta.url), "utf8"),
);

function stable(value) {
  if (Array.isArray(value)) return value.map(stable);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, stable(value[key])]),
    );
  }
  return value;
}

const { snapshot_sha256: expectedSnapshotSha256, ...body } = snapshot;
const canonical = `${JSON.stringify(stable(body))}\n`;
const actualSnapshotSha256 = crypto
  .createHash("sha256")
  .update(canonical, "utf8")
  .digest("hex")
  .toUpperCase();
const rows = snapshot.rows;
const activeRows = rows.filter((row) => row.status === "IN_PROGRESS");
const finalHilRows = rows.filter((row) => row.panel_role === "PHYSICALLY_FINAL_HIL");
const publicOrders = rows.map((row) => row.row);
const expectedOrders = Array.from(
  { length: snapshot.task_count },
  (_, index) => index + snapshot.row_start,
);
const finalHil = finalHilRows[0];
const planMetadata = publicMetadata.plan_lane;

const assertions = {
  canonical_plan_lane: snapshot.canonical_authority === "PLAN_LANE",
  exact_contiguous_public_orders:
    JSON.stringify(publicOrders) === JSON.stringify(expectedOrders) &&
    publicOrders.at(-1) === snapshot.row_end,
  exact_status_counts:
    rows.filter((row) => row.status === "COMPLETED").length === snapshot.status_counts.completed &&
    activeRows.length === snapshot.status_counts.in_progress &&
    rows.filter((row) => row.status === "PENDING").length === snapshot.status_counts.pending,
  one_exact_active_row:
    activeRows.length === 1 &&
    activeRows[0].row === snapshot.active_row &&
    activeRows[0].task_id === snapshot.active_task_id,
  physically_final_hil:
    finalHilRows.length === 1 &&
    finalHil?.row === snapshot.physically_final_hil_row &&
    finalHil?.task_id === snapshot.physically_final_hil_task_id &&
    finalHil?.status === "PENDING" &&
    rows.at(-1)?.row === finalHil?.row,
  persistence_law:
    snapshot.persistent_until === "NEXT_SIX_WAY_HIL_PRESENTED",
  snapshot_sha256_matches: actualSnapshotSha256 === expectedSnapshotSha256,
  public_metadata_matches:
    planMetadata.canonical_authority === snapshot.canonical_authority &&
    planMetadata.live_projection_rows === snapshot.task_count &&
    planMetadata.row_start === snapshot.row_start &&
    planMetadata.row_end === snapshot.row_end &&
    planMetadata.active_public_row === snapshot.active_row &&
    planMetadata.physically_final_hil_public_row === snapshot.physically_final_hil_row &&
    planMetadata.website_plan_snapshot_sha256 === snapshot.snapshot_sha256 &&
    planMetadata.executable_projection_sha256 === snapshot.executable_projection_sha256,
};

const receipt = {
  schema: "evidence-lane.current-execution-panel-test-receipt.v2",
  status: Object.values(assertions).every(Boolean) ? "PASS" : "FAIL",
  canonical_authority: snapshot.canonical_authority,
  public_row_count: rows.length,
  first_public_row: rows[0]?.row,
  sole_active_public_row: activeRows[0]?.row ?? null,
  physically_final_hil_public_row: finalHil?.row ?? null,
  executable_projection_sha256: snapshot.executable_projection_sha256,
  website_plan_snapshot_sha256: actualSnapshotSha256,
  canonical_bytes: Buffer.byteLength(canonical),
  assertions,
};

process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
if (receipt.status !== "PASS") process.exitCode = 1;

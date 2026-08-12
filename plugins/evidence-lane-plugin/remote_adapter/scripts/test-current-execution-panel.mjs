import crypto from "node:crypto";
import fs from "node:fs";
import vm from "node:vm";

const sourcePath = new URL("../app/_data/website-current-execution.ts", import.meta.url);
const source = fs.readFileSync(sourcePath, "utf8");
const marker = "export const websiteCurrentExecution: readonly WebsiteExecutionRow[] = ";
const start = source.indexOf(marker) + marker.length;
const end = source.indexOf("] as const;", start) + 1;

if (start < marker.length || end <= start) {
  throw new Error("Could not isolate the Current execution row projection.");
}

const rows = vm.runInNewContext(
  `(${source.slice(start, end)})`,
  Object.create(null),
  { timeout: 1_000 },
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

const payload = stable({
  schema: "evidence-lane.live-task-panel.v1",
  sealed_origin_receipt_panel_sha256:
    "9533BFC6C96F4E3FC95DC1B5D9D9981EA78957B821CAA9AF90D895C2C6F36A40",
  sealed_origin_public_projection_sha256:
    "B3E3C620F95DAA59B9E0206C69EBBF42AE4E1E2B142F3B60E81BBFB4E83B31D0",
  governed_prefix_positions: 8,
  public_rows: rows,
});
const canonical = JSON.stringify(payload);
const actualSha256 = crypto
  .createHash("sha256")
  .update(canonical, "utf8")
  .digest("hex")
  .toUpperCase();
const expectedSha256 = source.match(/livePanelSha256: "([A-F0-9]{64})"/)?.[1];
const activeRows = rows.filter((row) => row.status === "IN PROGRESS");
const publicOrders = rows.map((row) => row.order);
const expectedOrders = Array.from({ length: 116 }, (_, index) => index + 81);
const panelCorrection = rows.find((row) => row.order === 195);
const finalHil = rows.at(-1);

const assertions = {
  exact_public_orders: JSON.stringify(publicOrders) === JSON.stringify(expectedOrders),
  one_active_row_184: activeRows.length === 1 && activeRows[0].order === 184,
  panel_correction_row_195:
    panelCorrection?.deltaId ===
      "ADDITIVE_V150_PROJECT_PANEL_LANES_HIL_RENDER_CORRECTION_20260810" &&
    panelCorrection?.correctionDeltaId ===
      "ADDITIVE_LINEAR_GROWTH_ROW196_FINAL_HIL_CORRECTION_20260810",
  final_hil_row_196:
    finalHil?.order === 196 &&
    finalHil?.status === "PENDING" &&
    finalHil?.summary.includes("PHYSICALLY AND SEMANTICALLY FINAL SIX-WAY HIL"),
  governed_position_count_124: rows.length + 8 === 124,
  live_panel_sha256_matches: actualSha256 === expectedSha256,
};

const receipt = {
  schema: "evidence-lane.current-execution-panel-test-receipt.v1",
  status: Object.values(assertions).every(Boolean) ? "PASS" : "FAIL",
  public_row_count: rows.length,
  governed_position_count: rows.length + 8,
  first_public_row: rows[0]?.order,
  sole_active_public_row: activeRows[0]?.order ?? null,
  last_pre_hil_public_row: panelCorrection?.order ?? null,
  physically_final_hil_public_row: finalHil?.order ?? null,
  live_panel_sha256: actualSha256,
  canonical_bytes: Buffer.byteLength(canonical),
  assertions,
};

process.stdout.write(`${JSON.stringify(receipt, null, 2)}\n`);
if (receipt.status !== "PASS") process.exitCode = 1;

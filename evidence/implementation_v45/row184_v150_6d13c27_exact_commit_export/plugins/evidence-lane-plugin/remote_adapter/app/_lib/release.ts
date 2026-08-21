export type ReleaseState = {
  expected: string | null;
  deployed: string | null;
  durableOrigin: boolean;
  adapterReady: boolean;
};

export function releaseState(): ReleaseState {
  const expected = process.env.EVIDENCE_LANE_RELEASE_SHA?.trim().toLowerCase() ?? "";
  const deployed = process.env.VERCEL_GIT_COMMIT_SHA?.trim().toLowerCase() ?? "";
  const durable = process.env.EVIDENCE_LANE_DURABLE_MCP_ORIGIN?.trim() ?? "";
  const validExpected = /^[0-9a-f]{40}$/.test(expected);
  const validDeployed = /^[0-9a-f]{40}$/.test(deployed);
  const exactSha = validExpected && (!validDeployed || deployed === expected);
  const durableOrigin = durable.startsWith("https://");

  return {
    expected: validExpected ? expected : null,
    deployed: validDeployed ? deployed : null,
    durableOrigin,
    adapterReady: exactSha && durableOrigin,
  };
}

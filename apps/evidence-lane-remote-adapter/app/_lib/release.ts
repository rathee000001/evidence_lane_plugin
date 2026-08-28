export type ReleaseState = {
  expected: string | null;
  deployed: string | null;
  siteIdentityReady: boolean;
};

export function releaseState(): ReleaseState {
  const expected = process.env.EVIDENCE_LANE_RELEASE_SHA?.trim().toLowerCase() ?? "";
  const deployed = process.env.VERCEL_GIT_COMMIT_SHA?.trim().toLowerCase() ?? "";
  const validExpected = /^[0-9a-f]{40}$/.test(expected);
  const validDeployed = /^[0-9a-f]{40}$/.test(deployed);
  const exactSha = validExpected && (!validDeployed || deployed === expected);

  return {
    expected: validExpected ? expected : null,
    deployed: validDeployed ? deployed : null,
    siteIdentityReady: exactSha,
  };
}

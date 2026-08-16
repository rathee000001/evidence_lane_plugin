import { repositoryUrl } from "./site";

export const releaseVersion = "2.2.0";

const deployedCommitCandidate =
  process.env.VERCEL_GIT_COMMIT_SHA ?? process.env.NEXT_PUBLIC_EVIDENCE_LANE_RELEASE_SHA ?? "";

export const deployedCommit = /^[0-9a-f]{40}$/i.test(deployedCommitCandidate)
  ? deployedCommitCandidate.toLowerCase()
  : null;

export const releaseIdentity = {
  version: releaseVersion,
  commit: deployedCommit,
  shortCommit: deployedCommit?.slice(0, 12) ?? "awaiting deployed Git SHA",
  commitUrl: deployedCommit ? `${repositoryUrl}/commit/${deployedCommit}` : `${repositoryUrl}/commits/main`,
  source: deployedCommit ? "VERCEL_GIT_COMMIT_SHA" : "UNPUBLISHED_SOURCE_FALLBACK",
  propagationContract: "GITHUB_MARKDOWN_TO_SITE_FOOTERS_DELTA_TABLE_VERCEL_AND_EXISTING_DEVPOST",
} as const;

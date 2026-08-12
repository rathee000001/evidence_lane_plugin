import Link from "next/link";

import { releaseState } from "../_lib/release";

export function ReleaseStatus({ compact = false }: { compact?: boolean }) {
  const release = releaseState();
  return (
    <div className={`releaseStatus ${release.siteIdentityReady ? "ready" : "blocked"} ${compact ? "compact" : ""}`}>
      <span className="statusDot" aria-hidden="true" />
      <div>
        <strong>{release.siteIdentityReady ? "Documentation release identity exact" : "Documentation release identity unsealed"}</strong>
        <p>
          {release.siteIdentityReady
            ? "The expected Git SHA matches the deployed documentation build."
            : "The website may render, but exact Git identity has not been supplied and matched."}
        </p>
        <code>release {release.expected?.slice(0, 12) ?? "not configured"} / deploy {release.deployed?.slice(0, 12) ?? "not reported"}</code>
        {!compact ? <Link href="/connect">Read the installation boundary</Link> : null}
      </div>
    </div>
  );
}

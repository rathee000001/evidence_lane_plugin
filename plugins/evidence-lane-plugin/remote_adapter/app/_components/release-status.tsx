import Link from "next/link";

import { releaseState } from "../_lib/release";

export function ReleaseStatus({ compact = false }: { compact?: boolean }) {
  const release = releaseState();
  return (
    <div className={`releaseStatus ${release.adapterReady ? "ready" : "blocked"} ${compact ? "compact" : ""}`}>
      <span className="statusDot" aria-hidden="true" />
      <div>
        <strong>{release.adapterReady ? "ChatGPT MCP edge configured" : "ChatGPT MCP edge fail-closed"}</strong>
        <p>
          {release.adapterReady
            ? "A durable HTTPS origin and the exact release identity are configured."
            : "The public website is available; MCP remains blocked until the durable origin and exact Git release identity are configured."}
        </p>
        <code>release {release.expected?.slice(0, 12) ?? "not configured"} / deploy {release.deployed?.slice(0, 12) ?? "not reported"}</code>
        {!compact ? <Link href="/connect">Read the deployment boundary</Link> : null}
      </div>
    </div>
  );
}

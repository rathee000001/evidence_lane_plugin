import Link from "next/link";

import { releaseIdentity } from "../_data/release-identity";
import { ownerSocialLinks, primaryNavigation } from "../_data/site";

export function SiteFooter() {
  return (
    <footer className="footer shell">
      <div className="footerLead">
        <strong>Evidence Lane</strong>
        <p>Independent R&amp;D by Praveen Rathee, built through human-directed AI-assisted engineering and review.</p>
        <p>Copyright &copy; 2026 Praveen Rathee. All rights reserved.</p>
        <span>Evidence before promotion. Human authority before truth.</span>
        <p
          className="footerReleaseIdentity"
          data-release-version={releaseIdentity.version}
          data-release-commit={releaseIdentity.commit ?? "UNPUBLISHED"}
          data-release-source={releaseIdentity.source}
        >
          Release <strong>{releaseIdentity.version}</strong> &middot; Git <Link href={releaseIdentity.commitUrl}>{releaseIdentity.shortCommit}</Link>
        </p>
      </div>
      <div className="footerMap">
        <div>
          <strong>Explore</strong>
          {primaryNavigation.map((item) => <Link href={item.href} key={item.href}>{item.label}</Link>)}
        </div>
        <div>
          <strong>Policies</strong>
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms and conditions</Link>
          <Link href="/license">License</Link>
          <Link href="/copyright">Copyright</Link>
          <Link href="/third-party">Third-party licenses and rights</Link>
          <Link href="/security">Security</Link>
          <Link href="/support">Support</Link>
        </div>
        <div>
          <strong>Repository</strong>
          <Link href="/readme">README</Link>
          <Link href="/architecture">Architecture</Link>
          <Link href="/skills">Skills</Link>
          <Link href="/mcp">Native MCP</Link>
          <Link href="/hooks">Hooks</Link>
          <Link href="/provenance#upstream-reference-ledger">Upstream provenance</Link>
        </div>
        <div>
          <strong>User guides</strong>
          <Link href="/tunnel">User Tunnel Guide</Link>
        </div>
        <div>
          <strong>Contributors</strong>
          <Link href="/credits">Human contributors</Link>
        </div>
        <div>
          <strong>Praveen Rathee</strong>
          {ownerSocialLinks.map((item) => <Link href={item.href} key={item.href}>{item.label}</Link>)}
        </div>
      </div>
    </footer>
  );
}

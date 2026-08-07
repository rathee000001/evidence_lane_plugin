import Link from "next/link";

import { ownerSocialLinks, primaryNavigation } from "../_data/site";

export function SiteFooter() {
  return (
    <footer className="footer shell">
      <div className="footerLead">
        <strong>Evidence Lane</strong>
        <p>Independent R&amp;D by Praveen Rathee, built through human-directed AI-assisted engineering and review.</p>
        <p>Copyright &copy; 2026 Praveen Rathee. All rights reserved.</p>
        <span>Evidence before promotion. Human authority before truth.</span>
      </div>
      <div className="footerMap">
        <div>
          <strong>Explore</strong>
          {primaryNavigation.map((item) => <Link href={item.href} key={item.href}>{item.label}</Link>)}
        </div>
        <div>
          <strong>Policies</strong>
          <Link href="/privacy">Privacy</Link>
          <Link href="/terms">Terms</Link>
          <Link href="/support">Support</Link>
        </div>
        <div>
          <strong>Repository</strong>
          <Link href="/readme">README</Link>
          <Link href="/license">License</Link>
          <Link href="/copyright">Copyright</Link>
          <Link href="/security">Security</Link>
          <Link href="/provenance#upstream-reference-ledger">Upstream provenance</Link>
        </div>
        <div>
          <strong>Contributors</strong>
          <Link href="/credits">Contributors</Link>
        </div>
        <div>
          <strong>Praveen Rathee</strong>
          {ownerSocialLinks.map((item) => <Link href={item.href} key={item.href}>{item.label}</Link>)}
        </div>
      </div>
    </footer>
  );
}

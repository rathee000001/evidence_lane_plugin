import Link from "next/link";

import { humanContributions } from "../_data/contributors";
import { primaryNavigation } from "../_data/site";

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
          <Link href="https://github.com/rathee000001/evidence_lane_plugin#readme">README</Link>
          <Link href="/license">License</Link>
          <Link href="/copyright">Copyright</Link>
          <Link href="https://github.com/rathee000001/evidence_lane_plugin/blob/main/SECURITY.md">Security</Link>
          <Link href="https://github.com/rathee000001/evidence_lane_plugin/blob/main/docs/UPSTREAM_REFERENCE_PROVENANCE.md">Upstream provenance</Link>
        </div>
        <div>
          <strong>Contributors</strong>
          <Link href="/credits">Full contribution record</Link>
          {humanContributions.map((person) => <Link href={person.href} key={person.name}>{person.name}</Link>)}
        </div>
      </div>
    </footer>
  );
}

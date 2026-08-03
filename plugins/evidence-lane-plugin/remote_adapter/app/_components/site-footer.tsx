import Link from "next/link";

import { primaryNavigation } from "../_data/site";

export function SiteFooter() {
  return (
    <footer className="footer shell">
      <div className="footerLead">
        <strong>Evidence Lane</strong>
        <p>Independent R&amp;D by Praveen Rathee, built through human-directed AI-assisted engineering and review.</p>
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
      </div>
    </footer>
  );
}

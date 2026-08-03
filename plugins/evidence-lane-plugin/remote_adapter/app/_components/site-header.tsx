import Image from "next/image";
import Link from "next/link";

import { primaryNavigation } from "../_data/site";

export function SiteHeader() {
  return (
    <header className="siteHeader">
      <nav className="nav shell" aria-label="Primary navigation">
        <Link className="brand" href="/" aria-label="Evidence Lane home">
          <Image src="/evidence-cube-icon.png" alt="" width={44} height={44} priority />
          <span>
            <strong>Evidence Lane</strong>
            <small>Inspectable project memory</small>
          </span>
        </Link>
        <div className="navLinks">
          {primaryNavigation.map((item) => (
            <Link href={item.href} key={item.href}>{item.label}</Link>
          ))}
        </div>
        <Link className="navCta" href="/connect">Connection status</Link>
      </nav>
    </header>
  );
}

"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { primaryNavigation } from "../_data/site";

export function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="siteHeader">
      <nav className="rilFloatingNav" aria-label="Primary navigation">
        <Link className="brand" href="/" aria-label="Evidence Lane home">
          <Image
            className="floatingBrandLogo"
            src="/evidence-lane-full-logo.png"
            alt="Evidence Lane"
            width={2400}
            height={1792}
            priority
          />
        </Link>
        <div className="navPillCluster">
          <div className="navLinks" role="list">
            {primaryNavigation.map((item) => (
              <Link
                className={pathname === item.href ? "active" : ""}
                href={item.href}
                key={item.href}
                role="listitem"
              >
                {item.label}
              </Link>
            ))}
          </div>
          <Link className="navCta" href="/connect">
            <span aria-hidden="true" />
            HIL status
          </Link>
        </div>
      </nav>
    </header>
  );
}

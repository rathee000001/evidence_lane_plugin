"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { primaryNavigation } from "../_data/site";
import { GlassIconOrb, OfficialToolIcon, type OfficialToolIconName } from "./evidence-assets";

const navIdentity: readonly { color: string; icon: OfficialToolIconName }[] = [
  { color: "#69d9f5", icon: "node" },
  { color: "#69d9f5", icon: "pulse" },
  { color: "#83ddb3", icon: "database" },
  { color: "#b6a0ff", icon: "terminal" },
  { color: "#f2a1c5", icon: "node" },
  { color: "#efca72", icon: "package" },
  { color: "#7fc9ef", icon: "git" },
  { color: "#9ed368", icon: "docker" },
] as const;

export function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="siteHeader">
      <nav className="rilFloatingNav" aria-label="Primary navigation">
        <div className="brand" aria-hidden="true">
          <Image
            className="floatingBrandLogo"
            src="/evidence-lane-full-logo.png"
            alt=""
            width={2400}
            height={1792}
            priority
          />
        </div>
        <div className="navPillCluster">
          <div className="navLinks" role="list">
            {primaryNavigation.map((item, index) => (
              <Link
                className={`navGlassPill universal-pill${pathname === item.href ? " active" : ""}`}
                href={item.href}
                key={item.href}
                role="listitem"
                data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001"
                data-pill-content-mode="text"
                data-pill-containment="no-overlap"
              >
                <GlassIconOrb color={navIdentity[index].color} size={28} decorative>
                  <OfficialToolIcon tool={navIdentity[index].icon} size={15} decorative />
                </GlassIconOrb>
                <span>{item.label}</span>
              </Link>
            ))}
          </div>
          <Link className="navCta" href="/hil">
            <GlassIconOrb color="#efca72" size={30} decorative>
              <OfficialToolIcon tool="package" size={16} decorative />
            </GlassIconOrb>
            <b>HIL status</b>
          </Link>
        </div>
      </nav>
    </header>
  );
}

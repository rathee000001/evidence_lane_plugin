"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { primaryNavigation } from "../_data/site";
import { GlassIconOrb, OfficialToolIcon, type OfficialToolIconName } from "./evidence-assets";

type PrimaryNavigationHref = (typeof primaryNavigation)[number]["href"];

const navIdentity = {
  "/": { color: "#69d9f5", icon: "node" },
  "/skills": { color: "#a99af7", icon: "package" },
  "/mcp": { color: "#83ddb3", icon: "terminal" },
  "/hooks": { color: "#f2a1c5", icon: "pulse" },
  "/commands": { color: "#efca72", icon: "terminal" },
  "/architecture": { color: "#69d9f5", icon: "pulse" },
  "/lanes": { color: "#83ddb3", icon: "database" },
  "/operators": { color: "#b6a0ff", icon: "terminal" },
  "/studio": { color: "#f2a1c5", icon: "node" },
  "/proof": { color: "#efca72", icon: "package" },
  "/provenance": { color: "#7fc9ef", icon: "git" },
  "/connect": { color: "#9ed368", icon: "docker" },
} satisfies Readonly<
  Record<PrimaryNavigationHref, { color: string; icon: OfficialToolIconName }>
>;

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
            {primaryNavigation.map((item) => (
              <Link
                className={`navGlassPill universal-pill${pathname === item.href ? " active" : ""}`}
                href={item.href}
                key={item.href}
                role="listitem"
                data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001"
                data-pill-content-mode="text"
                data-pill-containment="no-overlap"
              >
                <GlassIconOrb color={navIdentity[item.href].color} size={28} decorative>
                  <OfficialToolIcon tool={navIdentity[item.href].icon} size={15} decorative />
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

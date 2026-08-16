import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { FloatingEvidenceStudio } from "./_components/floating-evidence-studio";
import { SiteAtmosphere } from "./_components/site-atmosphere";
import { SiteFooter } from "./_components/site-footer";
import { SiteHeader } from "./_components/site-header";
import { releaseIdentity } from "./_data/release-identity";
import { publicSiteUrl, repositoryUrl } from "./_data/site";

export const metadata: Metadata = {
  metadataBase: new URL(publicSiteUrl),
  title: {
    default: "Evidence Lane | Human-governed project memory",
    template: "%s | Evidence Lane",
  },
  description:
    "Evidence Lane turns project sources into inspectable SQLite, Mermaid, DOT, pointer, and HIL evidence without silently promoting candidate truth.",
  applicationName: "Evidence Lane",
  keywords: ["evidence governance", "project memory", "SQLite", "provenance", "human in the loop"],
  alternates: { canonical: "/" },
  authors: [{ name: "Praveen Rathee", url: "https://www.linkedin.com/in/praveen-rathee-8b028030b/" }],
  creator: "Praveen Rathee",
  publisher: "Praveen Rathee",
  category: "Developer Tools",
  referrer: "origin-when-cross-origin",
  icons: { icon: "/evidence-lane-icon.png", apple: "/evidence-lane-icon.png" },
  openGraph: {
    type: "website",
    url: publicSiteUrl,
    siteName: "Evidence Lane",
    title: "Evidence Lane",
    description: "Inspectable project memory with human-controlled acceptance.",
    images: [{ url: "/evidence-lane-full-logo.png", width: 2400, height: 1792, alt: "Evidence Lane full logo" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Evidence Lane",
    description: "Inspectable project memory with human-controlled acceptance.",
    images: ["/evidence-lane-full-logo.png"],
  },
  other: {
    "evidence-lane:repository": repositoryUrl,
    "evidence-lane:mcp-endpoint": "https://mcp.evidencelane.org/mcp",
    "evidence-lane:release-version": releaseIdentity.version,
    "evidence-lane:release-commit": releaseIdentity.commit ?? "UNPUBLISHED",
    "evidence-lane:release-propagation": releaseIdentity.propagationContract,
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <SiteAtmosphere />
        <SiteHeader />
        {children}
        <FloatingEvidenceStudio />
        <SiteFooter />
      </body>
    </html>
  );
}

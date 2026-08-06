import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { SiteAtmosphere } from "./_components/site-atmosphere";
import { SiteFooter } from "./_components/site-footer";
import { SiteHeader } from "./_components/site-header";
import { publicSiteUrl } from "./_data/site";

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
  authors: [{ name: "Praveen Rathee" }],
  creator: "Praveen Rathee",
  icons: { icon: "/evidence-lane-icon.png", apple: "/evidence-lane-icon.png" },
  openGraph: {
    type: "website",
    title: "Evidence Lane",
    description: "Inspectable project memory with human-controlled acceptance.",
    images: [{ url: "/evidence-lane-full-logo.png", width: 2400, height: 1792, alt: "Evidence Lane full logo" }],
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <SiteAtmosphere />
        <SiteHeader />
        {children}
        <SiteFooter />
      </body>
    </html>
  );
}

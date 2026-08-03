import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";
import { SiteFooter } from "./_components/site-footer";
import { SiteHeader } from "./_components/site-header";

export const metadata: Metadata = {
  metadataBase: new URL("https://evidence-lane-chatgpt-mcp-adapter.vercel.app"),
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
  icons: { icon: "/evidence-cube-icon.png", apple: "/evidence-cube-icon.png" },
  openGraph: {
    type: "website",
    title: "Evidence Lane",
    description: "Inspectable project memory with human-controlled acceptance.",
    images: [{ url: "/evidence-root-fibers.png", width: 1600, height: 900, alt: "Evidence Lane provenance topology" }],
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <SiteHeader />
        {children}
        <SiteFooter />
      </body>
    </html>
  );
}

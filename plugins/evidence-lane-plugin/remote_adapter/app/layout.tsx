import type { Metadata } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "Evidence Lane | Human-governed project memory",
  description:
    "Evidence Lane turns project sources into inspectable SQLite, Mermaid, DOT, pointer, and HIL evidence without silently promoting candidate truth.",
  icons: { icon: "/evidence-lane-icon.png" },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}

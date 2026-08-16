import type { MetadataRoute } from "next";

import { publicSiteUrl } from "./_data/site";

const paths = [
  "",
  "/skills",
  "/mcp",
  "/hooks",
  "/commands",
  "/architecture",
  "/lanes",
  "/operators",
  "/studio",
  "/proof",
  "/provenance",
  "/connect",
  "/hil",
  "/readme",
  "/privacy",
  "/terms",
  "/support",
  "/license",
  "/copyright",
  "/third-party",
  "/credits",
  "/security",
  "/helper",
  "/tunnel",
];

export default function sitemap(): MetadataRoute.Sitemap {
  return paths.map((path) => ({
    url: `${publicSiteUrl}${path}`,
    changeFrequency: path ? "monthly" : "weekly",
    priority: path ? 0.7 : 1,
  }));
}

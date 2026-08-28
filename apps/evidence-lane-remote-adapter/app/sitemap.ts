import type { MetadataRoute } from "next";

import { publicSiteUrl } from "./_data/site";

const paths = [
  "",
  "/memory",
  "/canon",
  "/ai-learning",
  "/skills",
  "/mcp",
  "/hooks",
  "/plan",
  "/git-ci",
  "/architecture",
  "/lanes",
  "/operators",
  "/studio",
  "/proof",
  "/provenance",
  "/release",
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
  "/tunnel",
];

export default function sitemap(): MetadataRoute.Sitemap {
  return paths.map((path) => ({
    url: `${publicSiteUrl}${path}`,
    changeFrequency: path ? "monthly" : "weekly",
    priority: path ? 0.7 : 1,
  }));
}

import type { MetadataRoute } from "next";

import { publicSiteUrl } from "./_data/site";

const paths = ["", "/architecture", "/lanes", "/operators", "/studio", "/proof", "/provenance", "/connect", "/privacy", "/terms", "/support", "/license", "/copyright", "/credits"];

export default function sitemap(): MetadataRoute.Sitemap {
  return paths.map((path) => ({
    url: `${publicSiteUrl}${path}`,
    changeFrequency: path ? "monthly" : "weekly",
    priority: path ? 0.7 : 1,
  }));
}

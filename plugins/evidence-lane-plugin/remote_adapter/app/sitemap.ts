import type { MetadataRoute } from "next";

const paths = ["", "/architecture", "/lanes", "/proof", "/provenance", "/connect", "/privacy", "/terms", "/support"];

export default function sitemap(): MetadataRoute.Sitemap {
  return paths.map((path) => ({
    url: `https://evidence-lane-chatgpt-mcp-adapter.vercel.app${path}`,
    changeFrequency: path ? "monthly" : "weekly",
    priority: path ? 0.7 : 1,
  }));
}

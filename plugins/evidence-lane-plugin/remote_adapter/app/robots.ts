import type { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: ["/mcp"] },
    sitemap: "https://evidence-lane-chatgpt-mcp-adapter.vercel.app/sitemap.xml",
  };
}

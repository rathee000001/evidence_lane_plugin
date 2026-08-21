import type { MetadataRoute } from "next";

import { publicSiteUrl } from "./_data/site";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: ["/mcp"] },
    sitemap: `${publicSiteUrl}/sitemap.xml`,
  };
}

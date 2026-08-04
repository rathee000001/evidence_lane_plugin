import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Evidence Lane",
    short_name: "Evidence Lane",
    description: "Inspectable project memory with human-controlled acceptance.",
    start_url: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#125cdd",
    icons: [{ src: "/evidence-lane-icon.png", sizes: "256x256", type: "image/png" }],
  };
}

import Link from "next/link";

import { GlassIconOrb, OfficialToolIcon } from "./_components/evidence-assets";

export default function NotFound() {
  return (
    <main className="notFound shell">
      <span className="kicker">Route not found</span>
      <h1>This path is outside the public Evidence Lane surface.</h1>
      <p>Use the website navigation for human-readable pages. MCP clients should connect to the documented protocol endpoint.</p>
      <Link className="primary universal-pill actionGlassPill" href="/" data-universal-pill-schema="T023_UNIVERSAL_GLASS_PILL_V001"><GlassIconOrb color="#69d9f5" size={30} decorative><OfficialToolIcon tool="pulse" size={16} decorative /></GlassIconOrb><span>Return home</span></Link>
    </main>
  );
}

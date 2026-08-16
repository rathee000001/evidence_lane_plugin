import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Third-party licenses and rights" };

export default function ThirdPartyPage() {
  return (
    <main className="legal shell">
      <Link href="/">&larr; Evidence Lane</Link>
      <span className="kicker">Independent rights</span>
      <h1>Third-party licenses and rights</h1>
      <p>Evidence Lane&apos;s proprietary license applies only to original Evidence Lane material. Dependencies, tools, services, models, assets, trademarks, and upstream references retain their own licenses, notices, and ownership.</p>
      <h2>Package notices</h2>
      <p>The Git-tracked package notice identifies bundled and referenced third-party components. The dependency audit separately records license evidence, corrections, and unresolved review boundaries.</p>
      <h2>No rights transfer</h2>
      <p>Including, testing, citing, or interoperating with a third-party component does not transfer that component&apos;s rights to Evidence Lane and does not transfer Evidence Lane ownership to its provider.</p>
    </main>
  );
}

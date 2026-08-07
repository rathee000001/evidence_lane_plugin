import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Copyright" };

export default function CopyrightPage() {
  return (
    <main className="legal shell">
      <Link href="/">&larr; Evidence Lane</Link>
      <span className="kicker">Separate copyright record</span>
      <h1>Copyright</h1>
      <p>Copyright &copy; 2026 Praveen Rathee. All rights reserved.</p>
      <h2>Ownership</h2>
      <p>Evidence Lane is conceived, directed, funded, and owned by Praveen Rathee. Copyright covers the original source expression, documentation, artwork, schemas, website presentation, lifecycle specifications, and project-specific artifacts, subject to the separate rights of third-party components.</p>
      <h2>Third-party rights boundary</h2>
      <p>Third-party software, services, models, assets, and trademarks remain governed by their respective owners&apos; terms, licenses, and rights. Evidence Lane grants no rights over them.</p>
      <h2>Human authority</h2>
      <p>AI systems and software tools assist implementation and review; they do not own the project, accept candidates, authorize source changes, or receive copyright authorship. Crediting a reviewer or tool records provenance and does not transfer ownership or HIL authority.</p>
      <h2>Reuse boundary</h2>
      <p>Do not reproduce or distribute protected material without written permission. Review the separate <Link href="/license">License</Link> for access terms and <Link href="/credits">Credits</Link> for attributable human and toolchain roles. The source-controlled <Link href="https://github.com/rathee000001/evidence_lane_plugin/blob/main/COPYRIGHT.md">COPYRIGHT.md</Link> is the canonical repository record for a released commit.</p>
    </main>
  );
}

import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "License" };

export default function LicensePage() {
  return (
    <main className="legal shell">
      <Link href="/">&larr; Evidence Lane</Link>
      <span className="kicker">Canonical rights notice</span>
      <h1>Proprietary source license</h1>
      <p>Copyright &copy; 2026 Praveen Rathee. All rights reserved.</p>
      <h2>Protected material</h2>
      <p>The Evidence Lane source, documentation, original artwork, schemas, lifecycle design, and project-specific artifacts are proprietary unless a file expressly states otherwise. Access for evaluation, private collaboration, or testing does not grant permission to copy, redistribute, publish, sublicense, commercialize, create derivative releases, or expose private project material.</p>
      <h2>No transfer of rights</h2>
      <p>No patent, trademark, copyright, trade-secret, database, or other intellectual-property right is transferred by access to the repository or website. No HIL decision, candidate package, Git branch, installation, or deployment changes that rule.</p>
      <h2>Third-party software</h2>
      <p>Third-party software, services, models, assets, and trademarks remain governed by their respective owners&apos; terms, licenses, and rights. Evidence Lane grants no rights over them. Their inclusion does not transfer ownership of Evidence Lane, and this notice does not replace their terms. Model and tool assistance does not create project authorship or acceptance authority.</p>
      <h2>Additional permission</h2>
      <p>Permission beyond private evaluation requires a separate written agreement from Praveen Rathee. The repository copy of <Link href="https://github.com/rathee000001/evidence_lane_plugin/blob/main/LICENSE.md">LICENSE.md</Link> is the source-controlled record for a released commit.</p>
    </main>
  );
}

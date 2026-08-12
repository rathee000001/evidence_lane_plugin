import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = { title: "Privacy" };

export default function PrivacyPage() {
  return (
    <main className="legal shell">
      <Link href="/">← Evidence Lane</Link>
      <span className="kicker">Public policy</span>
      <h1>Privacy</h1>
      <p>The public website stores no Evidence Lane project data and does not expose a lifecycle MCP endpoint. Vercel files, logs, and memory are never accepted-state authority.</p>
      <h2>Data handled</h2>
      <p>When connected, the durable Evidence Lane service may process source material and visible chat context that a user intentionally supplies. Source policy excludes governed secret, credential, environment, runtime, and untracked operational material before indexing. Hidden chain-of-thought and private model reasoning are never Evidence Lane inputs.</p>
      <h2>Retention and control</h2>
      <p>Retention, deletion, access control, queueing, and audit receipts are governed by the configured durable service. If that service, authentication, storage, or exact release identity cannot be verified, the edge blocks the request.</p>
      <h2>Public assets</h2>
      <p>The website may serve static images, styles, and documentation. Those assets do not contain a user project brain, accepted pointer, or private source package.</p>
    </main>
  );
}

import Link from "next/link";

export default function NotFound() {
  return (
    <main className="notFound shell">
      <span className="kicker">Route not found</span>
      <h1>This path is outside the public Evidence Lane surface.</h1>
      <p>Use the website navigation for human-readable pages. MCP clients should connect to the documented protocol endpoint.</p>
      <Link className="primary" href="/">Return home</Link>
    </main>
  );
}

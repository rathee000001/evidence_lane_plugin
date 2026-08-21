import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { GovernedStoryExplorer } from "../_components/governed-story-explorer";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "GitHub and SDK Delivery",
  description: "Evidence Lane source intake, exact commit, GitHub CI, SDK delivery, and Vercel preview evidence for v3.0.",
};

const deliveryProof = [
  ["Source intake", "Tracked source, commit ancestry, branch identity, and bounded Git facts enter one project SQLite evidence route.", "git"],
  ["GitHub and SDK", "The GitHub App and internal SDK bind short-lived provider access to an exact governed commit and artifact scope.", "pulse"],
  ["Vercel preview", "The full current site build, routes, ordered navigation, and page depth bind the same commit; preview success remains non-HIL.", "package"],
] as const;

const deliveryStages = [
  {
    id: "intake",
    label: "Git source intake",
    summary: "Measure the branch, HEAD, tree, tracked diff, and relevant untracked identities before delivery.",
    outcome: "A bounded Git evidence snapshot stored for faster exact-source queries without replacing the repository.",
    proof: "Branch/ref, full commit SHA, tree SHA, path-set hashes, content hashes, ancestry, and source-intake receipt.",
    boundary: "The separate GitHub provider may fetch or publish, but raw provider calls do not become Evidence Lane actions.",
    details: [
      "Resolve the exact repository and worktree identity before staging or remote work.",
      "Preserve excluded historical evidence and unrelated untracked bytes instead of sweeping them into a commit.",
      "Store bounded Git facts in the project SQLite route so later queries can avoid repeated broad scans.",
    ],
    icon: "git",
    color: "#83ddb3",
  },
  {
    id: "commit",
    label: "Exact branch commit",
    summary: "Create one complete current-v3.0 commit whose docs, catalog, code, tests, and public site agree.",
    outcome: "A reviewed commit on the governed v3.0 branch with no historical-version comparison story in current public copy.",
    proof: "Staged path manifest, commit SHA, catalog counts, test receipts, package/source hashes, and clean index result.",
    boundary: "A branch commit is delivery evidence, not a Project candidate, HIL result, main merge, or accepted PV.",
    details: [
      "Stage only the intended v3.0 release files and preserve unrelated dirty/untracked material.",
      "Verify every version, action count, navigation link, README statement, and artifact identity against current source.",
      "Commit only after local package, site, Python, and generated-index gates pass against the same bytes.",
    ],
    icon: "package",
    color: "#69d9f5",
  },
  {
    id: "github",
    label: "GitHub checks",
    summary: "Publish and inspect checks against the exact governed branch commit through scoped provider routes.",
    outcome: "Commit-bound CI, workflow, artifact, and provider receipts with failures preserved rather than hidden.",
    proof: "GitHub App installation identity, short-lived token scope, check-run SHA, workflow conclusion, and artifact hashes.",
    boundary: "Provider action counts and Evidence Lane governed-routing counts are reported separately and never reparented.",
    details: [
      "Request only the repository and operation scope required for this exact delivery.",
      "Bind every check and artifact to the full commit SHA; reject branch-only or latest-build substitutions.",
      "Record a failure as evidence and continue only through the predeclared non-HIL recovery rule.",
    ],
    icon: "pulse",
    color: "#a99af7",
  },
  {
    id: "preview",
    label: "Full Vercel preview",
    summary: "Build every v3.0 page and verify the full navigation, content depth, interactions, and responsive layout.",
    outcome: "A PREVIEW deployment for the exact commit with the 17-destination primary nav and full-depth product story.",
    proof: "Build output, route manifest, browser checks, popup keyboard/focus checks, deployment state, target, URL, and commit SHA.",
    boundary: "Vercel remains its own provider group; a READY preview is not production, HIL, package install, or pointer movement.",
    details: [
      "Refresh every current page, home story, sitemap, ordered navbar, deep links, and newly introduced v3.0 sections.",
      "Verify the deliberate wrapped navbar has no horizontal scrolling and all detail popups support keyboard close and focus return.",
      "Seal only a PREVIEW receipt and reject any accidental production target.",
    ],
    icon: "node",
    color: "#efca72",
  },
  {
    id: "stable",
    label: "Stable Git fallback",
    summary: "Deliver the exact verified commit into the stable Git slot through the GitHub Apps plus SDK route.",
    outcome: "A content-identical branch fallback that can recover a broken mutable local-testing layer.",
    proof: "Commit export hash, package hash, GitHub/SDK authorization receipt, installed catalog, and slot identity.",
    boundary: "The main-merge recovery slot remains unchanged until its later explicit Project HIL and promotion route.",
    details: [
      "Export and package the exact committed tree rather than the live dirty worktree.",
      "Install through the governed Git delivery route and verify the native 88-action, 17-skill catalog after reattach.",
      "Keep hooks disabled except for each independently repaired and installed lifecycle event.",
    ],
    icon: "terminal",
    color: "#f2a1c5",
  },
] as const;

const previewChecklist = [
  "Home story reflects current v3.0 capabilities and boundaries.",
  "Primary navigation is exactly Home → Architecture → Lanes → Operators → Memory → Canon → AI Learning → GitHub/SDK Delivery → Skills → MCP → Hooks → Commands → Proof → Provenance → Connect → Prompt Studio → HIL.",
  "The desktop navigation deliberately wraps to two rows with no horizontal scrollbar; responsive layouts remain contained.",
  "Memory, Canon, AI Learning, GitHub/SDK, Plan, and Release pages carry multi-section business depth and inspectable popups.",
  "Popup focus enters the dialog, stays trapped, closes by Escape/backdrop/control, and returns to its trigger.",
  "Provider ownership keeps Evidence Lane, GitHub, Vercel, Render, and other raw action groups separate.",
] as const;

export default function GitCiPage() {
  return (
    <main>
      <PageHero eyebrow="GitHub and SDK delivery" title="One exact v3.0 commit connects source, checks, package, and preview." description="Evidence Lane records governed delivery evidence while GitHub and Vercel remain distinct providers. Every result must bind the same full commit SHA, and none becomes lifecycle authority." aside={<HeroOrbit preset="connect" />} />

      <section className="section shell"><div className="sectionHead wideHead"><span className="kicker">Commit-bound delivery</span><h2>Every public result points back to the same reviewed bytes.</h2><p>The public story is v3.0 itself—not a comparison with an older release. Source, docs, package catalog, GitHub checks, Vercel routes, and the installed fallback must agree.</p></div><div className="routeGrid">{deliveryProof.map(([title, detail, icon], index) => <article className="routeCard" key={title}><span className="compactDepthPill"><GlassIconOrb color={["#83ddb3", "#69d9f5", "#efca72"][index]} size={28} decorative><OfficialToolIcon tool={icon} size={15} decorative /></GlassIconOrb><span>{String(index + 1).padStart(2, "0")}</span></span><h3>{title}</h3><p>{detail}</p></article>)}</div></section>

      <section className="section governedStoryBand"><div className="shell"><GovernedStoryExplorer eyebrow="Inspectable delivery flow" title="Trace one commit from source intake to recoverable fallback." description="Open a stage to inspect the provider proof, governed receipt, and exact non-HIL boundary." items={deliveryStages} /></div></section>

      <section className="section shell deliveryChecklist"><div className="sectionHead wideHead"><span className="kicker">Full-preview contract</span><h2>A route existing is not enough; the whole v3.0 story must work.</h2></div><ol>{previewChecklist.map((item, index) => <li key={item}><span>{String(index + 1).padStart(2, "0")}</span><p>{item}</p></li>)}</ol></section>

      <section className="section architectureDark"><div className="shell"><div className="sectionHead wideHead"><span className="kicker light">Delivery boundary</span><h2>Commit, CI, SDK delivery, and preview are evidence—not acceptance.</h2><p>They do not infer a HIL token, create or accept a Project candidate, merge main, enable unverified hooks, or move the accepted pointer. The branch commit may become the governed stable fallback only through its exact Git delivery route.</p></div></div></section>
    </main>
  );
}

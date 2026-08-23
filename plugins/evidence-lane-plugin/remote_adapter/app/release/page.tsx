import type { Metadata } from "next";

import { GlassIconOrb, OfficialToolIcon } from "../_components/evidence-assets";
import { GovernedStoryExplorer } from "../_components/governed-story-explorer";
import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";
import { currentProductContract } from "../_data/current-product-contract";

export const metadata: Metadata = {
  title: "Release",
  description: "Evidence Lane v3.0 local testing, per-Delta layers, branch fallback, main release, hooks, and HIL-gated promotion.",
};

const releaseLayers = [
  ["Local testing", "Only a canonical Delta that explicitly owns installation may replace the mutable local-testing layer and reattach the exact task. LOCAL_PREVIEW_ONLY work never installs.", "package"],
  ["Branch fallback", "The exact governed branch commit becomes the stable Git-delivered fallback only after commit, checks, package, SDK, and install proofs pass.", "git"],
  ["Main release", "The main-merge slot remains unchanged until the exact final release route and its human Project HIL authorize promotion.", "pulse"],
] as const;

const releaseStages = [
  {
    id: "delta",
    label: "Verify Delta layer",
    summary: "Prove one active Delta's source, tests, package, and declared acceptance checks before installation.",
    outcome: "A content-addressed local-layer candidate tied to the exact row, source snapshot, and test evidence.",
    proof: "Delta ID, source/test hashes, package/catalog hashes, changed-path roles, and verification receipt.",
    boundary: "This is per-Delta local verification, not Project candidate creation, HIL, or pointer movement.",
    details: [
      "Resolve the active row and its relevant authority snapshot before assessing its output.",
      "Run the row's focused tests plus package/catalog parity against the exact changed files.",
      "Reject a package whose source, catalog, skills, hooks, or build identity differs from the verified work.",
    ],
    icon: "node",
    color: "#69d9f5",
  },
  {
    id: "testing",
    label: "Install local testing",
    summary: "Install the verified newer v3.0 layer into the existing mutable testing slot without adding another slot.",
    outcome: "An attached local package whose source, catalog, runtime, profile, and exact Task8 identity agree.",
    proof: `Installer receipt, slot path, build identity, ${currentProductContract.nativeMcp.totalActions}-action/${currentProductContract.governedSkillCount}-skill catalog, task UUID, runtime profile, and reattach receipt.`,
    boundary: "ENV/UOP, accepted PV, the Git fallback, and main-release slot remain unchanged.",
    details: [
      "Use the established local installer and existing cache-busted testing slot.",
      "Restart only when host catalog replacement requires it, then reopen the same task without duplication.",
      "Verify Boot/Flash and the exact native catalog before resuming the active Goal row.",
    ],
    icon: "package",
    color: "#83ddb3",
  },
  {
    id: "hooks",
    label: "Progressive hooks",
    summary: "Repair, test, install, and enable each lifecycle hook independently across all eleven registered events.",
    outcome: "A progressive installed-host proof that finishes with all eleven corrected hooks ON.",
    proof: "Per-hook source hash, package hash, registered event, hidden Windows invocation, observed host call, and rollback receipt.",
    boundary: "Unverified hooks remain off; no event is reported usable until its own installed-host proof passes.",
    details: [
      "Test one hook against its real host event and confirm no visible terminal window appears.",
      "Enable each verified hook through native API control and keep every passing event ON.",
      "If one event fails, disable only it, repair and retest it, then restore the all-eleven-ON steady state.",
    ],
    icon: "pulse",
    color: "#f2a1c5",
  },
  {
    id: "commit",
    label: "Commit branch fallback",
    summary: "Commit the complete current-v3.0 release and verify GitHub CI plus full Vercel preview against that SHA.",
    outcome: "A governed branch commit whose code, docs, catalog, package, site, and provider evidence agree.",
    proof: "Commit SHA, workflow/check receipts, PREVIEW deployment receipt, route/browser verification, and exact package export.",
    boundary: "Green checks and a READY preview do not merge main or infer Project HIL.",
    details: [
      "Stage the intended release set while preserving unrelated historical dirty and untracked bytes.",
      "Publish through scoped GitHub provider access and verify all checks against the full commit SHA.",
      "Refresh every v3.0 website page and interaction in the commit-bound Vercel preview.",
    ],
    icon: "git",
    color: "#a99af7",
  },
  {
    id: "fallback",
    label: "Install Git fallback",
    summary: "Use the GitHub Apps plus internal SDK route to install the exact committed package into the stable fallback slot.",
    outcome: "A recoverable stable slot byte-identical to the verified branch commit while the older main slot remains intact.",
    proof: "GitHub authorization scope, SDK action receipt, exact-commit export, package hash, installed catalog, and slot selection proof.",
    boundary: "This fallback can recover broken local testing, but it is not the final main release or accepted Project PV.",
    details: [
      "Build from the exact commit export rather than the mutable working tree.",
      "Authorize the stable-slot delivery through the governed GitHub App and SDK path only.",
      "Verify byte identity and retain the prior main-merge recovery slot until later human promotion.",
    ],
    icon: "terminal",
    color: "#efca72",
  },
] as const;

export default function ReleasePage() {
  return (
    <main>
      <PageHero eyebrow="Release channels" title="Testing, branch fallback, and main release keep separate identities." description="Evidence Lane v3.0 uses content-addressed packages and explicit slot receipts so a local Delta repair cannot silently rewrite the Git fallback, enable an unverified hook, or replace the main-release identity." aside={<HeroOrbit preset="connect" />} />

      <section className="section shell"><div className="sectionHead wideHead"><span className="kicker">Three measured layers</span><h2>Promote exact bytes. Preserve every fallback boundary.</h2><p>The mutable local slot advances after verified Deltas. The exact branch commit can become the stable recovery slot through GitHub Apps and SDK. The main slot waits for its later explicit Project HIL.</p></div><div className="routeGrid">{releaseLayers.map(([title, detail, icon], index) => <article className="routeCard" key={title}><span className="compactDepthPill"><GlassIconOrb color={["#69d9f5", "#83ddb3", "#efca72"][index]} size={28} decorative><OfficialToolIcon tool={icon} size={15} decorative /></GlassIconOrb><span>{String(index + 1).padStart(2, "0")}</span></span><h3>{title}</h3><p>{detail}</p></article>)}</div></section>

      <section className="section governedStoryBand"><div className="shell"><GovernedStoryExplorer eyebrow="Inspectable release flow" title="See how one verified Delta becomes a recoverable v3.0 layer." description="Each stage exposes the exact bytes, provider proof, slot effect, hook boundary, and non-HIL flags." items={releaseStages} /></div></section>

      <section className="section shell depthContractGrid">
        <article><span className="kicker">Recovery order</span><h2>Local testing first, stable Git fallback second, main unchanged.</h2><p>If a newer local layer breaks, select the exact committed stable fallback. Do not create a fourth slot or rewrite the earlier main-merge recovery package.</p></article>
        <article><span className="kicker">Hook safety</span><h2>Activation follows installed-host proof one event at a time.</h2><p>Explicit native API controls remain available while hooks are off. The UI must not imply that any unverified event is available or has been used.</p></article>
      </section>

      <section className="section architectureDark"><div className="shell"><div className="sectionHead wideHead"><span className="kicker light">Promotion gate</span><h2>Installed bytes and green checks do not move Project Truth.</h2><p>The local layer, branch fallback, and main-release slot retain separate receipts. Only the exact later Project HIL and promotion route may change the accepted pointer or main-release identity.</p></div></div></section>
    </main>
  );
}

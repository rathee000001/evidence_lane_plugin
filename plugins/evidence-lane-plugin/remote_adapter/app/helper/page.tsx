import type { Metadata } from "next";

import { HeroOrbit } from "../_components/hero-orbit";
import { PageHero } from "../_components/page-hero";

export const metadata: Metadata = {
  title: "User Helper Guide",
  description: "The project-scoped Windows Goal Recovery helper and its authority boundary.",
};

export default function HelperPage() {
  return (
    <main>
      <PageHero
        eyebrow="User helper"
        title="Recover the exact task binding. Do not turn recovery into release authority."
        description="The user Goal Recovery helper is project-scoped, deep-link bound, hidden on Windows, and separate from the maintainer updater."
        aside={<HeroOrbit preset="connect" />}
      />
      <section className="section shell">
        <div className="sectionHead wideHead"><span className="kicker">One-time setup</span><h2>Register, inspect, recover, or remove one exact binding.</h2><p>The supported actions are Probe, Register, RecoverNow, RecoverAtLogon, Status, and Unregister. Identity comes from the project ID, task ID, deep link, release, and package—not a title or working directory.</p></div>
        <div className="compareGrid">
          <article><h3>What it may do</h3><p>Retain multiple project bindings and request the exact Codex task deep link after normal Windows sign-in.</p></article>
          <article><h3>What it must prove</h3><p>Hidden launch, exact binding, scheduled-task identity, and separate host-observed Plan and Changes restoration receipts.</p></article>
          <article><h3>What it cannot do</h3><p>HIL, Fuse, pointer movement, Git, installation, slot switching, publication, or navigation during an active State Travel lease.</p></article>
        </div>
      </section>
    </main>
  );
}

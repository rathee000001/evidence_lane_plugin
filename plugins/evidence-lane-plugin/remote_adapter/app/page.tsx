const controls = ["Boot", "Rollback", "Build", "Refresh", "Mode", "Source Intake"];

const lanes = [
  "Discussion",
  "Analysis",
  "Plan",
  "Mode",
  "Local code",
  "GitHub code",
  "Documents",
  "Data / Excel",
  "Presentations",
  "PDF / OCR",
  "Images / OCR",
  "Artifacts",
  "Custom",
  "Brain loader",
  "Research",
  "Project Engulf",
  "SQLite brain",
  "Chat Lineage",
];

function releaseState() {
  const expected = process.env.EVIDENCE_LANE_RELEASE_SHA?.trim().toLowerCase() ?? "";
  const deployed = process.env.VERCEL_GIT_COMMIT_SHA?.trim().toLowerCase() ?? "";
  const durable = process.env.EVIDENCE_LANE_DURABLE_MCP_ORIGIN?.trim() ?? "";
  const exactSha = /^[0-9a-f]{40}$/.test(expected) && (!deployed || deployed === expected);
  const durableHttps = durable.startsWith("https://");
  return {
    expected: /^[0-9a-f]{40}$/.test(expected) ? expected : null,
    deployed: /^[0-9a-f]{40}$/.test(deployed) ? deployed : null,
    adapterReady: exactSha && durableHttps,
  };
}

export default function Home() {
  const release = releaseState();
  return (
    <main>
      <nav className="nav shell" aria-label="Primary navigation">
        <a className="brand" href="#top" aria-label="Evidence Lane home">
          <img src="/evidence-lane-icon.png" alt="" width="42" height="42" />
          <span>Evidence Lane</span>
        </a>
        <div className="navLinks">
          <a href="#system">System</a>
          <a href="#lanes">Lanes</a>
          <a href="#boundary">Boundary</a>
          <a href="/support">Support</a>
        </div>
      </nav>

      <section className="hero shell" id="top">
        <div className="eyebrow"><span /> Evidence before promotion</div>
        <img
          className="heroLogo"
          src="/evidence-os-full-logo.png"
          alt="Evidence Lane"
          width="1824"
          height="1376"
        />
        <h1>Build an inspectable project brain. Keep acceptance human.</h1>
        <p className="lede">
          Evidence Lane routes project sources into content-addressed, searchable
          SQLite sectors with Mermaid and DOT topology, exact pointers, lineage,
          and rollback evidence. A candidate never becomes accepted truth without
          the governed HIL decision.
        </p>
        <div className="actions">
          <a className="primary" href="#system">Inspect the architecture</a>
          <a className="secondary" href="/healthz">Adapter health JSON</a>
        </div>
        <div className={`release ${release.adapterReady ? "ready" : "blocked"}`}>
          <span className="statusDot" />
          <div>
            <strong>{release.adapterReady ? "ChatGPT edge configured" : "ChatGPT edge fail-closed"}</strong>
            <p>
              {release.adapterReady
                ? "Durable HTTPS origin and exact release identity are configured."
                : "The website is available; MCP remains blocked until durable HTTPS storage/auth and an exact Git SHA are configured."}
            </p>
            <code>
              release {release.expected?.slice(0, 12) ?? "not configured"} · deploy {release.deployed?.slice(0, 12) ?? "not reported"}
            </code>
          </div>
        </div>
      </section>

      <section className="section shell" id="system">
        <div className="sectionHead">
          <span className="kicker">One lifecycle</span>
          <h2>Parallel evidence work. Serial authority.</h2>
          <p>
            Independent lane computation can run concurrently. Candidate sealing,
            Fuse, accepted pointers, rollback, and State Travel remain ordered and
            compare-and-swap governed.
          </p>
        </div>
        <div className="controlGrid">
          {controls.map((control, index) => (
            <article className="controlCard" key={control}>
              <span>{String(index + 1).padStart(2, "0")}</span>
              <h3>{control}</h3>
              <p>{[
                "Verify runtime, locked Flash, host class, and durable storage atomically.",
                "Move only the accepted pointer across immutable accepted versions.",
                "Seal an unaccepted candidate and stop at the six-way human gate.",
                "Re-index changed sections while reusing unchanged content-addressed chunks.",
                "Apply ordered operating-mode intersections without changing source truth.",
                "Auto-detect or explicitly route sources across the canonical lane registry.",
              ][index]}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section band" id="lanes">
        <div className="shell">
          <div className="sectionHead compact">
            <span className="kicker">18 canonical lanes</span>
            <h2>Every lane leaves evidence you can open.</h2>
          </div>
          <div className="laneGrid">
            {lanes.map((lane, index) => (
              <div className="lane" key={lane}>
                <span>{String(index + 1).padStart(2, "0")}</span>{lane}
              </div>
            ))}
          </div>
          <div className="artifactRow" aria-label="Lane output contract">
            {[
              ["SQLite", "integrity, foreign keys, FTS and structured facts"],
              ["MMD + DOT", "semantic source-to-output topology"],
              ["Pointer", "entered-from and proposed-version evidence"],
              ["Receipt", "refresh, hash, tool and lifecycle classification"],
            ].map(([title, text]) => (
              <div key={title}><strong>{title}</strong><p>{text}</p></div>
            ))}
          </div>
        </div>
      </section>

      <section className="section shell boundary" id="boundary">
        <div>
          <span className="kicker">Deployment boundary</span>
          <h2>Vercel is the ChatGPT edge, not the Evidence Lane brain.</h2>
        </div>
        <div className="boundaryGrid">
          <article>
            <span className="pill blue">Codex</span>
            <h3>Native Git-installed plugin</h3>
            <p>Local SQLite and Git-backed authority stay on the durable user host. Vercel is not in this path.</p>
          </article>
          <article>
            <span className="pill gold">ChatGPT</span>
            <h3>Thin authenticated MCP edge</h3>
            <p>Vercel forwards only to one configured durable HTTPS origin after verifying the exact release SHA.</p>
          </article>
          <article>
            <span className="pill dark">Fail closed</span>
            <h3>No temporary truth</h3>
            <p>Missing auth, storage, queue, durable origin, or release identity blocks MCP. The landing page is not proof of connector readiness.</p>
          </article>
        </div>
      </section>

      <footer className="footer shell">
        <div><strong>Evidence Lane</strong><p>Independent R&amp;D by Praveen Rathee with AI-assisted engineering and review.</p></div>
        <div className="footerLinks"><a href="/privacy">Privacy</a><a href="/terms">Terms</a><a href="/support">Support</a></div>
      </footer>
    </main>
  );
}

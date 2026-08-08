"use client";

import { useMemo, useState } from "react";

import { deltaLedger, deltaLedgerBoundary, type DeltaPhase } from "../_data/delta-ledger";
import { GlassIconOrb, GlassPill, OfficialToolIcon } from "./evidence-assets";

type LedgerFilter = "All" | DeltaPhase;

const filters: readonly LedgerFilter[] = ["All", "Foundation", "V1.2 evolution", "V1.3 hardening", "Current execution"];
const filterColors = ["#69d9f5", "#83ddb3", "#efca72", "#b6a0ff", "#f2a1c5"] as const;

export function DeltaLedgerExplorer() {
  const [filter, setFilter] = useState<LedgerFilter>("All");
  const [expanded, setExpanded] = useState(false);
  const visible = useMemo(
    () => filter === "All" ? deltaLedger : deltaLedger.filter((entry) => entry.phase === filter),
    [filter],
  );

  return (
    <div className={`deltaLedgerExplorer ${expanded ? "is-expanded" : "is-collapsed"}`}>
      <div className="deltaLedgerToggleRow">
        <div>
          <span>One additive governed ledger</span>
          <strong>{deltaLedgerBoundary.totalRows} rows: {deltaLedgerBoundary.sealedHistoricalDeltaRows} sealed Deltas + {deltaLedgerBoundary.liveExecutionRows} live Plan Lane steps</strong>
          <small>The live steps are appended after row 80 in this same table; they are not accepted historical truth.</small>
        </div>
        <GlassPill
          active={expanded}
          tone="gold"
          aria-expanded={expanded}
          aria-controls="complete-delta-ledger-table"
          leading={
            <GlassIconOrb color="#efca72" size={38} decorative>
              <OfficialToolIcon tool="database" size={19} decorative />
            </GlassIconOrb>
          }
          onClick={() => setExpanded((current) => !current)}
        >
          {expanded ? "Collapse Delta ledger" : "Open Delta ledger"}
        </GlassPill>
      </div>

      {expanded ? (
        <div className="deltaLedgerTable" id="complete-delta-ledger-table">
          <div
            className="deltaLedgerFilters"
            role="toolbar"
            aria-label="Filter the complete Delta ledger"
            data-universal-pill-cluster="delta-ledger-filters"
          >
            {filters.map((item, index) => (
              <GlassPill
                key={item}
                active={filter === item}
                tone={index === 2 ? "gold" : "cyan"}
                aria-pressed={filter === item}
                leading={
                  <GlassIconOrb color={filterColors[index]} size={34} decorative>
                  <OfficialToolIcon tool={index === 0 ? "database" : index >= 3 ? "pulse" : "package"} size={18} decorative />
                  </GlassIconOrb>
                }
                onClick={() => setFilter(item)}
              >
                {item}<small>{item === "All" ? deltaLedger.length : deltaLedger.filter((entry) => entry.phase === item).length} rows</small>
              </GlassPill>
            ))}
          </div>

          <div className="deltaLedgerHeader">
            <span>Order</span><span>Exact governed Delta ID</span><span>Evidence state</span>
          </div>
          <ol className="deltaLedgerList" start={visible[0]?.order ?? 1} aria-live="polite">
            {visible.map((entry) => (
              <li className="deltaLedgerRow" key={entry.order} value={entry.order}>
                <span className="deltaOrder">{String(entry.order).padStart(3, "0")}</span>
                <div><code>{entry.id}</code><p>{entry.summary}</p></div>
                <span className={`deltaStatus deltaStatus${entry.status.replace(/[^A-Z]+/g, "-")}`}>{entry.status}</span>
              </li>
            ))}
          </ol>
          <p className="deltaLedgerBoundary">
            Rows 1&ndash;80 are sealed historical Delta evidence. Rows 81&ndash;87 project the current
            seven-step Plan Lane, including active step 73 and last step 67, into the same additive table. Neither class
            authorizes Fuse, accepted-pointer movement, a main merge, deployment, or human approval.
          </p>
        </div>
      ) : null}
    </div>
  );
}

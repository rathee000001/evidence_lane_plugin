"use client";

import { useMemo, useState } from "react";

import { deltaLedger, type DeltaPhase } from "../_data/delta-ledger";
import { GlassIconOrb, GlassPill, OfficialToolIcon } from "./evidence-assets";

type LedgerFilter = "All" | DeltaPhase;

const filters: readonly LedgerFilter[] = ["All", "Foundation", "V1.2 evolution", "V1.3 hardening"];
const filterColors = ["#69d9f5", "#83ddb3", "#efca72", "#b6a0ff"] as const;

export function DeltaLedgerExplorer() {
  const [filter, setFilter] = useState<LedgerFilter>("All");
  const visible = useMemo(
    () => filter === "All" ? deltaLedger : deltaLedger.filter((entry) => entry.phase === filter),
    [filter],
  );

  return (
    <div className="deltaLedgerExplorer">
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
                <OfficialToolIcon tool={index === 0 ? "database" : index === 3 ? "pulse" : "package"} size={18} decorative />
              </GlassIconOrb>
            }
            onClick={() => setFilter(item)}
          >
            {item}<small>{item === "All" ? 80 : deltaLedger.filter((entry) => entry.phase === item).length} rows</small>
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
        Ledger status records evidence state only. It does not authorize Fuse, accepted-pointer movement,
        a main merge, production deployment, or human approval.
      </p>
    </div>
  );
}

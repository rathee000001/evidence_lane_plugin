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
  const filterCount = (item: LedgerFilter) => {
    if (item === "All") return `${deltaLedgerBoundary.totalRows} public rows`;
    if (item === "Current execution") {
      return `${deltaLedgerBoundary.liveExecutionRows} rows`;
    }
    return `${deltaLedger.filter((entry) => entry.phase === item).length} rows`;
  };

  return (
    <div className={`deltaLedgerExplorer ${expanded ? "is-expanded" : "is-collapsed"}`}>
      <div className="deltaLedgerToggleRow">
        <div>
          <span>One additive governed ledger</span>
          <strong>{deltaLedgerBoundary.totalRows} public rows: {deltaLedgerBoundary.sealedHistoricalDeltaRows} sealed Deltas + {deltaLedgerBoundary.liveExecutionRows} Current execution rows</strong>
          <small>
            Current execution is the exact flat, consecutive live 081&ndash;196 projection over the immutable State Travel origin.
            Every row keeps its full human-readable contract; Rows 191&ndash;195 also expose their native Delta and event receipts.
          </small>
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
                {item}<small>{filterCount(item)}</small>
              </GlassPill>
            ))}
          </div>

          <div className="deltaLedgerHeader">
            <span>Order</span><span>Exact governed Delta ID</span><span>Evidence state</span>
          </div>
          <ol className="deltaLedgerList" start={visible[0]?.order ?? 1} aria-live="polite">
            {visible.map((entry) => (
              <li className="deltaLedgerRow" key={`${entry.phase}-${entry.order}`} value={entry.order}>
                <span className="deltaOrder">{String(entry.order).padStart(3, "0")}</span>
                <div>
                  <code>{entry.id}</code>
                  <p>{entry.summary}</p>
                  {entry.deltaSha256 ? <small>Delta SHA-256: <code>{entry.deltaSha256}</code></small> : null}
                  {entry.eventSha256 ? <small>Event SHA-256: <code>{entry.eventSha256}</code></small> : null}
                  {entry.correctionDeltaId ? (
                    <small>
                      Linked correction: <code>{entry.correctionDeltaId}</code><br />
                      Delta SHA-256: <code>{entry.correctionDeltaSha256}</code><br />
                      Event SHA-256: <code>{entry.correctionEventSha256}</code>
                    </small>
                  ) : null}
                </div>
                <span className={`deltaStatus deltaStatus${entry.status.replace(/[^A-Z]+/g, "-")}`}>{entry.status}</span>
              </li>
            ))}
          </ol>
          <p className="deltaLedgerBoundary">
            Rows 1&ndash;80 remain the unchanged sealed historical Delta evidence. Public rows
            81&ndash;196 are the consecutive live Current execution projection: {deltaLedgerBoundary.liveExecutionRows} full rows,
            with {deltaLedgerBoundary.currentExecutionCompleted} completed,
            {` ${deltaLedgerBoundary.currentExecutionActive}`} active, and {` ${deltaLedgerBoundary.currentExecutionPending}`} pending.
            Public row {deltaLedgerBoundary.activePublicOrder} / public task position {deltaLedgerBoundary.activeTaskPosition} / governed receipt position {deltaLedgerBoundary.activeReceiptPosition} is active;
            public row {deltaLedgerBoundary.finalSweepPublicOrder} / public task position {deltaLedgerBoundary.finalSweepTaskPosition} / governed receipt position {deltaLedgerBoundary.finalSweepReceiptPosition} is the final fresh sweep;
            public row {deltaLedgerBoundary.lastPreHilPublicOrder} / public task position {deltaLedgerBoundary.lastPreHilTaskPosition} / governed receipt position {deltaLedgerBoundary.lastPreHilReceiptPosition} is the last additive pre-HIL row;
            public row {deltaLedgerBoundary.finalHilPublicOrder} / public task position {deltaLedgerBoundary.finalHilTaskPosition} / governed receipt position {deltaLedgerBoundary.finalHilReceiptPosition} is physically final.
            This projection does not authorize Fuse, accepted-pointer movement, main merge, production publication,
            Devpost mutation, or human approval.
          </p>
        </div>
      ) : null}
    </div>
  );
}

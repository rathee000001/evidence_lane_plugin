import type { CSSProperties } from "react";

import { GlassIconOrb } from "./evidence-assets";
import { SourceLaneIcon } from "./source-lane-icon";

const lanes = [
  ["discussion", "Discussion"],
  ["analysis", "Analysis"],
  ["plan", "Plan"],
  ["mode", "Mode"],
  ["local_code", "Local Code"],
  ["github_code", "GitHub Code"],
  ["docs", "Documents"],
  ["data_excel", "Data / Excel"],
  ["ppt", "Presentations"],
  ["pdf_ocr", "PDF / OCR"],
  ["images_ocr", "Images / OCR"],
  ["artifacts", "Artifacts"],
  ["custom", "Custom"],
  ["brain_loader", "Brain Loader"],
  ["research", "Research"],
  ["project_engulf", "Project Engulf"],
  ["sqlite_brain", "SQLite Brain"],
  ["chat_lineage", "Chat Lineage"],
] as const;

const accents = ["#63daf3", "#efc766", "#8d9df7", "#58dcb1", "#ed8db0", "#9fd26c"] as const;

type OrbitStyle = CSSProperties & {
  "--lane-angle": string;
  "--lane-angle-negative": string;
};

export function LaneOrbitAside() {
  return (
    <div className="laneOrbitAside" aria-label="18 canonical Evidence Lane source lanes">
      <div className="numberAside laneOrbitCore">
        <strong>18</strong>
        <span>canonical lanes</span>
        <small>plus ordered custom modes</small>
      </div>
      <div className="laneOrbitWheel" aria-hidden="true">
        {lanes.map(([lane, label], index) => {
          const angle = index * (360 / lanes.length);
          const style: OrbitStyle = {
            "--lane-angle": `${angle}deg`,
            "--lane-angle-negative": `${-angle}deg`,
          };
          return (
            <span className="laneOrbitItem" style={style} title={label} key={lane}>
              <span className="laneOrbitGlyph">
                <GlassIconOrb className="source-lane-orb" color={accents[index % accents.length]} size="clamp(32px,3vw,43px)" decorative>
                  <SourceLaneIcon lane={lane} size="56%" decorative />
                </GlassIconOrb>
              </span>
            </span>
          );
        })}
      </div>
    </div>
  );
}

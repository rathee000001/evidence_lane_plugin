import type { CSSProperties } from "react";

type GovernanceRing = {
  label: string;
  items: readonly string[];
  tone: "cyan" | "green" | "gold" | "violet";
};

function ringStyle(index: number, total: number) {
  return { "--map-angle": `${(index * 360) / total}deg` } as CSSProperties;
}

export function ConcentricGovernanceMap({
  eyebrow,
  center,
  centerDetail,
  rings,
}: {
  eyebrow: string;
  center: string;
  centerDetail: string;
  rings: readonly GovernanceRing[];
}) {
  return (
    <figure className="concentricGovernanceMap" aria-label={`${eyebrow}: ${center}`}>
      <div className="concentricMapCopy">
        <span>{eyebrow}</span>
        <strong>{center}</strong>
        <p>{centerDetail}</p>
      </div>
      <div className="concentricMapStage" aria-hidden="true">
        {rings.map((ring, ringIndex) => (
          <div className={`concentricMapRing ring${ringIndex + 1} tone${ring.tone}`} key={ring.label}>
            <small>{ring.label}</small>
            {ring.items.map((item, itemIndex) => (
              <span key={item} style={ringStyle(itemIndex, ring.items.length)}>{item}</span>
            ))}
          </div>
        ))}
        <div className="concentricMapCore"><i />{center}</div>
      </div>
      <figcaption>
        {rings.map((ring) => <span key={ring.label}><b>{ring.label}</b>{ring.items.join(" / ")}</span>)}
      </figcaption>
    </figure>
  );
}

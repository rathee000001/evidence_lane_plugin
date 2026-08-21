"use client";

import dynamic from "next/dynamic";

const AmbientEvidenceField = dynamic(
  () => import("./ambient-evidence-field"),
  { ssr: false },
);

export function SiteAtmosphere() {
  return <AmbientEvidenceField />;
}

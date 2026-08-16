"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import {
  repositoryDocumentForPath,
  repositoryDocumentUrl,
  repositorySourceReference,
} from "../_data/repository-documents";
import { GlassIconOrb, OfficialToolIcon } from "./evidence-assets";

export function RepositorySourceStrip() {
  const pathname = usePathname();
  const document = repositoryDocumentForPath(pathname);

  return (
    <aside
      className="repositorySourceStrip"
      data-authority-document={document.path}
      data-authority-ref={repositorySourceReference}
      aria-label={`Git-tracked Markdown authority for this page: ${document.path}`}
    >
      <GlassIconOrb color="#69d9f5" size={28} decorative>
        <OfficialToolIcon tool="git" size={15} decorative />
      </GlassIconOrb>
      <span><small>Git-tracked page authority</small><strong>{document.label}</strong></span>
      <code>{document.path}</code>
      <Link href={repositoryDocumentUrl(document)} target="_blank" rel="noreferrer">
        Open Markdown <span aria-hidden="true">↗</span>
      </Link>
    </aside>
  );
}

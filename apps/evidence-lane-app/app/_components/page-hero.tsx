import type { ReactNode } from "react";

type PageHeroProps = {
  eyebrow: string;
  title: string;
  description: string;
  aside?: ReactNode;
};

export function PageHero({ eyebrow, title, description, aside }: PageHeroProps) {
  return (
    <section className="pageHero orbitHeroFrame shell">
      <div>
        <span className="eyebrow"><i />{eyebrow}</span>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {aside ? <aside>{aside}</aside> : null}
    </section>
  );
}

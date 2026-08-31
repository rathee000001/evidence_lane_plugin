import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.esm.min.mjs";

const diagramCode = Array.from(
  document.querySelectorAll("pre code.language-mermaid, .language-mermaid pre code"),
).filter((node, index, rows) => rows.indexOf(node) === index);

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
  theme: "base",
  flowchart: { curve: "basis", htmlLabels: true, useMaxWidth: true },
  themeVariables: {
    background: "#fbfdff",
    primaryColor: "#e8f2ff",
    primaryTextColor: "#13213c",
    primaryBorderColor: "#1268e8",
    secondaryColor: "#f2ecff",
    secondaryTextColor: "#2e2354",
    secondaryBorderColor: "#7252c7",
    tertiaryColor: "#e8fbf8",
    tertiaryTextColor: "#123d3a",
    tertiaryBorderColor: "#087f77",
    lineColor: "#55749e",
    fontFamily: "Inter, Segoe UI, sans-serif",
    fontSize: "15px",
  },
});

function makeButton(label, text, action) {
  const button = document.createElement("button");
  button.type = "button";
  button.setAttribute("aria-label", label);
  button.textContent = text;
  button.addEventListener("click", action);
  return button;
}

for (const code of diagramCode) {
  const source = code.textContent || "";
  const original = code.closest(".highlighter-rouge") || code.closest("pre");
  if (!original || !source.trim()) continue;

  const card = document.createElement("figure");
  card.className = "diagram-card";
  const header = document.createElement("figcaption");
  header.className = "diagram-header";
  const label = document.createElement("strong");
  label.textContent = "Rendered workflow map";
  const tools = document.createElement("span");
  tools.className = "diagram-tools";
  const viewport = document.createElement("div");
  viewport.className = "diagram-viewport";
  const diagram = document.createElement("div");
  diagram.className = "mermaid";
  diagram.textContent = source;
  diagram.dataset.scale = "1";

  const resize = (delta) => {
    const current = Number(diagram.dataset.scale || "1");
    const next = delta === 0 ? 1 : Math.min(2.2, Math.max(0.65, current + delta));
    diagram.dataset.scale = String(next);
    diagram.style.width = `${next * 100}%`;
  };
  tools.append(
    makeButton("Zoom out", "−", () => resize(-0.15)),
    makeButton("Reset zoom", "100%", () => resize(0)),
    makeButton("Zoom in", "+", () => resize(0.15)),
  );
  header.append(label, tools);
  viewport.append(diagram);
  card.append(header, viewport);
  original.replaceWith(card);
}

if (diagramCode.length) {
  try {
    await mermaid.run({ querySelector: ".diagram-card .mermaid" });
  } catch (error) {
    for (const card of document.querySelectorAll(".diagram-card")) {
      const message = document.createElement("p");
      message.className = "diagram-error";
      message.textContent = "This workflow diagram could not be rendered.";
      card.querySelector(".diagram-viewport")?.replaceChildren(message);
    }
    console.error("Evidence Lane Mermaid rendering failed", error);
  }
}

document.addEventListener("click", (event) => {
  for (const menu of document.querySelectorAll(".docs-menu[open]")) {
    if (!menu.contains(event.target)) menu.removeAttribute("open");
  }
});

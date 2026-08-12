const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const baseUrl = (process.argv[2] || "http://127.0.0.1:3100").replace(/\/$/, "");
const outputDir = path.resolve(process.argv[3] || path.join(process.cwd(), "browser-verification"));
const chromeExecutable = process.env.EVIDENCE_LANE_CHROME || "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

const routes = ["/", "/lanes", "/operators", "/architecture", "/studio", "/proof", "/provenance", "/connect", "/readme", "/security", "/hil"];
const failures = [];
const observations = [];

function assert(condition, message) {
  if (!condition) failures.push(message);
}

function safeName(route) {
  return route === "/" ? "home" : route.slice(1).replace(/[^a-z0-9]+/gi, "-");
}

async function revealForScreenshot(page) {
  await page.addStyleTag({ content: ".section{content-visibility:visible!important;contain-intrinsic-size:auto!important}" });
  await page.evaluate(async () => {
    const step = Math.max(520, Math.floor(window.innerHeight * 0.72));
    const height = document.documentElement.scrollHeight;
    for (let y = 0; y < height; y += step) {
      window.scrollTo(0, y);
      await new Promise((resolve) => setTimeout(resolve, 35));
    }
    window.scrollTo(0, 0);
    await new Promise((resolve) => setTimeout(resolve, 120));
  });
}

async function visit(page, route, viewportName) {
  const consoleErrors = [];
  const pageErrors = [];
  const onConsole = (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  };
  const onPageError = (error) => pageErrors.push(error.message);
  page.on("console", onConsole);
  page.on("pageerror", onPageError);

  const response = await page.goto(`${baseUrl}${route}`, { waitUntil: "networkidle", timeout: 60_000 });
  assert(response && response.status() < 400, `${viewportName} ${route}: HTTP ${response ? response.status() : "NO_RESPONSE"}`);
  const bodyText = await page.locator("body").innerText();
  assert(bodyText.trim().length > 150, `${viewportName} ${route}: page body is unexpectedly empty`);
  const visibleErrorOverlays = await page
    .locator('nextjs-portal [data-nextjs-dialog-overlay], nextjs-portal [data-nextjs-toast-errors-parent]')
    .evaluateAll((nodes) => nodes.filter((node) => {
      const style = getComputedStyle(node);
      return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity || "1") !== 0;
    }).length);
  assert(visibleErrorOverlays === 0, `${viewportName} ${route}: Next.js error overlay is present`);
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  assert(dimensions.scrollWidth <= dimensions.clientWidth + 2, `${viewportName} ${route}: horizontal overflow ${dimensions.scrollWidth} > ${dimensions.clientWidth}`);
  assert(pageErrors.length === 0, `${viewportName} ${route}: page errors: ${pageErrors.join(" | ")}`);
  assert(consoleErrors.length === 0, `${viewportName} ${route}: console errors: ${consoleErrors.join(" | ")}`);
  observations.push({ route, viewport: viewportName, http_status: response?.status(), ...dimensions, console_errors: consoleErrors, page_errors: pageErrors });

  page.off("console", onConsole);
  page.off("pageerror", onPageError);
}

async function verifyDesktop(browser) {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  const page = await context.newPage();

  for (const route of routes) {
    await visit(page, route, "desktop");
    if (["/", "/lanes", "/operators", "/architecture", "/studio", "/proof", "/connect", "/readme", "/security"].includes(route)) {
      await revealForScreenshot(page);
      await page.screenshot({ path: path.join(outputDir, `desktop-${safeName(route)}.png`), fullPage: true });
    }
  }

  await page.goto(`${baseUrl}/`, { waitUntil: "networkidle" });
  const homeOrbit = page.locator(".evidenceOrbit");
  assert(await homeOrbit.isVisible(), "desktop home: 18/15/1 concentric governance map is not visible");
  assert(await homeOrbit.locator(".sourceLaneOrbit .evidenceOrbitNode").count() === 18, "desktop home: source-lane ring is not exactly 18 nodes");
  assert(await homeOrbit.locator(".pluginSurfaceOrbit .evidenceOrbitNode").count() === 15, "desktop home: plugin-surface ring is not exactly 15 nodes");
  assert(await homeOrbit.getByText("Human HIL", { exact: true }).isVisible(), "desktop home: one human HIL is missing from the center");
  await page.getByRole("heading", { name: "One additive ledger. 87 governed rows. No erased history." }).scrollIntoViewIfNeeded();
  await page.getByRole("button", { name: "Open Delta ledger" }).click();
  const ledger = page.locator(".deltaLedgerExplorer #complete-delta-ledger-table");
  assert(await ledger.isVisible(), "desktop home: combined Delta ledger did not open inside its explorer");
  assert(await ledger.locator(".deltaLedgerRow").count() === 87, `desktop home: expected 87 combined ledger rows, found ${await ledger.locator(".deltaLedgerRow").count()}`);
  await ledger.screenshot({ path: path.join(outputDir, "desktop-combined-delta-ledger.png") });

  await page.goto(`${baseUrl}/lanes`, { waitUntil: "networkidle" });
  assert(await page.getByRole("tab").count() === 18, `desktop lanes: expected 18 lane pills, found ${await page.getByRole("tab").count()}`);

  await page.goto(`${baseUrl}/proof`, { waitUntil: "networkidle" });
  assert(await page.locator(".proofLanePill").count() === 18, `desktop proof: expected 18 lane pills, found ${await page.locator(".proofLanePill").count()}`);
  assert(await page.locator(".laneProofDownloads a").count() === 4, `desktop proof: expected four canonical downloads, found ${await page.locator(".laneProofDownloads a").count()}`);
  const downloadLinks = await page.locator(".laneProofDownloads a").evaluateAll((links) => links.map((link) => link.href));
  for (const href of downloadLinks) {
    const response = await context.request.get(href);
    assert(response.ok(), `desktop proof: artifact download failed ${response.status()} ${href}`);
  }
  await page.getByRole("button", { name: /Open full-screen .* exact-MMD vector render/ }).click();
  assert(await page.locator(".proofLightbox").isVisible(), "desktop proof: 8K/vector full-screen viewer did not open");
  await page.getByRole("button", { name: "Zoom in" }).click();
  assert((await page.locator(".proofLightboxToolbar").innerText()).includes("135%"), "desktop proof: zoom-in control did not update the scale");
  await page.screenshot({ path: path.join(outputDir, "desktop-proof-lightbox.png"), fullPage: false });
  await page.keyboard.press("Escape");
  assert(!(await page.locator(".proofLightbox").count()), "desktop proof: Escape did not close the full-screen viewer");

  await page.goto(`${baseUrl}/connect`, { waitUntil: "networkidle" });
  const endpointCards = page.locator(".endpointCards a.endpointCard");
  assert(await endpointCards.count() === 3, `desktop connect: expected three linked endpoint cards, found ${await endpointCards.count()}`);
  const endpointEvidence = await endpointCards.evaluateAll((cards) => cards.map((card) => {
    const strong = card.querySelector("strong");
    const small = card.querySelector("small");
    return {
      href: card.href,
      strongColor: strong ? getComputedStyle(strong).color : null,
      smallColor: small ? getComputedStyle(small).color : null,
      background: getComputedStyle(card).backgroundImage,
    };
  }));
  assert(endpointEvidence.every((card) => card.href.startsWith("http") && card.strongColor && card.smallColor), "desktop connect: endpoint card link or readable-color evidence is incomplete");

  for (const route of ["/readme", "/security"]) {
    await page.goto(`${baseUrl}${route}`, { waitUntil: "networkidle" });
    const box = await page.locator("main.repositoryDocument").boundingBox();
    assert(box && box.width >= 1200, `desktop ${route}: expected full-width document, found ${box ? Math.round(box.width) : "NO_BOX"}px`);
  }

  await context.close();
}

async function verifyMobile(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  for (const route of routes) {
    await visit(page, route, "mobile");
    if (["/", "/proof", "/connect"].includes(route)) {
      await revealForScreenshot(page);
      await page.screenshot({ path: path.join(outputDir, `mobile-${safeName(route)}.png`), fullPage: true });
    }
  }
  await context.close();
}

(async () => {
  fs.mkdirSync(outputDir, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: chromeExecutable });
  try {
    await verifyDesktop(browser);
    await verifyMobile(browser);
  } finally {
    await browser.close();
  }

  const report = {
    schema: "evidence-lane-browser-verification-v1",
    generated_at: new Date().toISOString(),
    base_url: baseUrl,
    routes,
    status: failures.length ? "FAIL" : "PASS",
    failures,
    observations,
  };
  fs.writeFileSync(path.join(outputDir, "browser-verification.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");
  if (failures.length) {
    console.error(JSON.stringify(report, null, 2));
    process.exitCode = 1;
  } else {
    console.log(JSON.stringify({ status: report.status, routes: routes.length, observations: observations.length, output_dir: outputDir }, null, 2));
  }
})().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exitCode = 1;
});

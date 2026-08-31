import { chromium } from "@playwright/test";

const projectId = process.argv[2];
const appUrl = process.argv[3] ?? "http://127.0.0.1:3000";
if (!projectId) {
  throw new Error("Usage: node assert-preview-rendering.mjs <project-id> [app-url]");
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const runtimeErrors = [];
page.on("pageerror", (error) => runtimeErrors.push(error.message));
await page.goto(`${appUrl}/projects/${projectId}`, { waitUntil: "networkidle" });
const frameElement = await page.waitForSelector('iframe[title="分析报告"]', { timeout: 15_000 });
const frame = await frameElement.contentFrame();
if (!frame) throw new Error("Report iframe did not expose a content frame");
await frame.waitForSelector(".echarts-container svg", { timeout: 10_000 });

const result = await frame.evaluate(() => {
  const summary = document.querySelector(".summary")?.getBoundingClientRect();
  const section = document.querySelector(".section")?.getBoundingClientRect();
  const monthly = [...document.querySelectorAll(".chart-card")].find(
    (card) => card.querySelector("h3")?.textContent === "月度成交金额",
  );
  const ticks = [...(monthly?.querySelectorAll('.axis-tick[data-axis="left"]') ?? [])];
  return {
    documentUrl: location.href,
    chartCount: document.querySelectorAll(".echarts-container svg").length,
    monthlyTickLabels: ticks.map((node) => node.textContent),
    monthlyTickMetadata: ticks.map((node) => ({
      raw: node.getAttribute("data-raw-value"),
      scale: node.getAttribute("data-scale"),
      unit: node.getAttribute("data-unit"),
    })),
    summaryWidth: summary?.width ?? 0,
    sectionWidth: section?.width ?? 0,
  };
});

const iframeSrc = await frameElement.getAttribute("src");
await browser.close();
process.stdout.write(JSON.stringify({ ...result, iframeSrc, runtimeErrors }));

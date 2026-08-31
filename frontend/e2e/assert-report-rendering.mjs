import { pathToFileURL } from "node:url";
import { chromium } from "@playwright/test";

const reportPath = process.argv[2];
if (!reportPath) throw new Error("Usage: node assert-report-rendering.mjs <report.html>");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const runtimeErrors = [];
page.on("pageerror", (error) => runtimeErrors.push(error.message));
await page.goto(pathToFileURL(reportPath).href, { waitUntil: "load" });
await page.waitForSelector(".echarts-container svg, table, .editorial-narrative", { timeout: 10_000 });

const result = await page.evaluate(() => {
  const summary = document.querySelector(".summary")?.getBoundingClientRect();
  const section = document.querySelector(".section")?.getBoundingClientRect();
  const narrative = document.querySelector(".editorial-narrative")?.getBoundingClientRect();
  const chart = document.querySelector(".chart-card, .echarts-container")?.getBoundingClientRect();
  const group = document.querySelector(".visual-group");
  const groupItems = group ? [...group.children].map((node) => node.getBoundingClientRect()) : [];
  const charts = [...document.querySelectorAll(".echarts-container")];
  const xTicks = [...document.querySelectorAll(".category-label")];
  const svg = document.querySelector(".echarts-svg")?.getBoundingClientRect();
  const source = document.querySelector(".chart-card .source")?.getBoundingClientRect();
  const callout = document.querySelector(".callout")?.getBoundingClientRect();
  const overflowing = [...document.querySelectorAll("h1, h2, h3, .chart-card, table, .editorial-narrative, .callout")]
    .filter((node) => node.scrollWidth > node.clientWidth + 2).length;
  return {
    chartCount: charts.length,
    nonEmptyCharts: charts.filter((node) => node.querySelector("svg")).length,
    valueLabelCount: document.querySelectorAll(".value-label").length,
    dataPointCount: document.querySelectorAll(".data-point").length,
    xTickCount: xTicks.length,
    xTickRaw: xTicks.map((node) => node.getAttribute("data-raw-label")),
    xTickText: xTicks.map((node) => node.textContent),
    tickLabels: [...document.querySelectorAll('.axis-tick[data-axis="left"]')].map(
      (node) => node.textContent,
    ),
    tickMetadata: [...document.querySelectorAll('.axis-tick[data-axis="left"]')].map((node) => ({
      raw: node.getAttribute("data-raw-value"),
      scale: node.getAttribute("data-scale"),
      unit: node.getAttribute("data-unit"),
    })),
    summaryWidth: summary?.width ?? 0,
    sectionWidth: section?.width ?? 0,
    narrativeWidth: narrative?.width ?? 0,
    narrativeLeft: narrative?.left ?? 0,
    chartWidth: chart?.width ?? 0,
    chartLeft: chart?.left ?? 0,
    calloutLeft: callout?.left ?? 0,
    chartSvgHeight: svg?.height ?? 0,
    sourceGap: source && svg ? source.top - svg.bottom : null,
    visualGroupItemCount: groupItems.length,
    visualGroupTops: groupItems.map((box) => Math.round(box.top)),
    visualGroupClass: group?.className ?? "",
    tableText: document.querySelector("table")?.innerText ?? "",
    tableUsage: document.querySelector("[data-table-usage]")?.getAttribute("data-table-usage") ?? "",
    tableColumnCount: document.querySelectorAll("table thead th").length,
    overflowCount: overflowing,
    hasSectionTwoColumnGrid: document.documentElement.innerHTML.includes(
      "layout-two-column .blocks{display:grid",
    ),
  };
});

await browser.close();
process.stdout.write(JSON.stringify({ ...result, runtimeErrors }));

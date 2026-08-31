import { chromium } from "@playwright/test";
import fs from "node:fs/promises";

const projectId = process.env.PROJECT_ID ?? "pj_f7c3489791894825bbc3692d7f42a5b8";
const projectUrl = `http://localhost:3000/projects/${projectId}`;
const outputDir = "../artifacts/playwright";
const viewports = [
  { name: "desktop", width: 1440, height: 900 },
  { name: "mobile", width: 390, height: 844 },
];

await fs.mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport });
    const externalRequests = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (!['localhost', '127.0.0.1'].includes(url.hostname)) externalRequests.push(request.url());
    });
    const consoleErrors = [];
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });
    await page.goto(projectUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
    await page.waitForTimeout(2500);
    const bodyText = (await page.locator("body").innerText()).trim();
    if (bodyText.length < 80) throw new Error(`${viewport.name}: page is blank or incomplete`);
    const reportFrame = page.locator('iframe[title="分析报告"]');
    if (await reportFrame.count() !== 1) throw new Error(`${viewport.name}: report iframe missing`);
    await reportFrame.waitFor({ state: "visible", timeout: 10000 });
    const frame = page.frames().find((item) => item.url().includes("/api/projects/"));
    if (!frame) throw new Error(`${viewport.name}: report document did not load`);
    const reportText = (await frame.locator("body").innerText()).trim();
    if (reportText.length < 120) throw new Error(`${viewport.name}: report content is blank`);
    const chartCount = await frame.locator(".echarts-container").count();
    if (chartCount > 0 && await frame.locator(".echarts-svg").count() !== chartCount) {
      throw new Error(`${viewport.name}: chart container did not render an SVG`);
    }
    if (externalRequests.length) throw new Error(`${viewport.name}: external requests: ${externalRequests.join(", ")}`);
    await page.screenshot({ path: `${outputDir}/${viewport.name}.png`, fullPage: true });
    console.log(JSON.stringify({ viewport: viewport.name, bodyChars: bodyText.length, reportChars: reportText.length, chartCount, consoleErrors }));
    await page.close();
  }
} finally {
  await browser.close();
}

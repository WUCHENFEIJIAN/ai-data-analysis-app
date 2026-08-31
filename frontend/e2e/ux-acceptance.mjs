import { chromium } from "@playwright/test";
import fs from "node:fs/promises";

const projectId = "pj_ux_acceptance";
const projectUrl = `http://localhost:3000/projects/${projectId}`;
const outputDir = "../artifacts/playwright";
const viewports = [
  { name: "ux-desktop", width: 1440, height: 900 },
  { name: "ux-mobile", width: 390, height: 844 },
];

const run = {
  id: "run_ux_acceptance",
  project_id: projectId,
  user_request: "分析销售趋势",
  status: "running",
  state: "ANALYZE",
  step_count: 3,
  execution_count: 1,
  code_retry_count: 0,
  cancellation_requested: false,
  error_message: null,
  created_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:01Z",
};

const sseBody = [
  [1, "analysis.started", {}],
  [2, "analysis.plan_created", {}],
  [3, "analysis.execution_started", {}],
].map(([id, event, data]) => `id: ${id}\nevent: ${event}\ndata: ${JSON.stringify({ event, run_id: run.id, data })}\n\n`).join("");

await fs.mkdir(outputDir, { recursive: true });
const browser = await chromium.launch({ headless: true });
try {
  for (const viewport of viewports) {
    const page = await browser.newPage({ viewport });
    let submittedMessage = "";
    const browserErrors = [];
    page.on("console", (message) => {
      if (message.type() === "error") browserErrors.push(message.text());
    });
    page.on("pageerror", (error) => browserErrors.push(error.message));

    await page.route("**/api/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      const json = (body) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

      if (path === `/api/projects/${projectId}`) return json({ id: projectId, name: "体验验收项目", created_at: run.created_at, updated_at: run.updated_at });
      if (path === `/api/projects/${projectId}/files`) return json([{ name: "analysis", path: "analysis", kind: "directory", children: [{ name: "summary.json", path: "analysis/summary.json", kind: "file" }] }]);
      if (path === `/api/projects/${projectId}/artifacts`) return json([]);
      if (path === `/api/projects/${projectId}/messages`) return json([]);
      if (path === `/api/projects/${projectId}/analysis` && request.method() === "GET") return json(null);
      if (path === `/api/projects/${projectId}/analysis` && request.method() === "POST") {
        submittedMessage = JSON.parse(request.postData() ?? "{}").message ?? "";
        return json(run);
      }
      if (path === `/api/analysis/${run.id}`) return json(run);
      if (path === `/api/analysis/${run.id}/events`) return route.fulfill({ status: 200, contentType: "text/event-stream", body: sseBody });
      return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { message: "Not mocked" } }) });
    });

    await page.goto(projectUrl, { waitUntil: "networkidle", timeout: 30000 });
    await page.getByText("你好，我是你的数据分析助手。", { exact: false }).waitFor();

    const childFile = page.getByRole("button", { name: "summary.json" });
    await childFile.waitFor();
    await page.getByRole("button", { name: "折叠analysis" }).click();
    if (await childFile.count() !== 0) throw new Error(`${viewport.name}: directory did not collapse`);
    await page.getByRole("button", { name: "展开analysis" }).click();
    await childFile.waitFor();

    const composer = page.getByLabel("分析需求");
    const send = page.getByRole("button", { name: "发送" });
    const composerBox = await composer.boundingBox();
    const sendBox = await send.boundingBox();
    const sendParentBox = await send.locator("..").boundingBox();
    if (!composerBox || !sendBox || !sendParentBox || sendBox.x <= composerBox.x || sendBox.x + sendBox.width > sendParentBox.x + sendParentBox.width + 1) {
      throw new Error(`${viewport.name}: send button is not inside the composer at the right`);
    }

    await composer.fill("第一行");
    await composer.press("Shift+Enter");
    if (!(await composer.inputValue()).includes("\n")) throw new Error(`${viewport.name}: Shift+Enter did not insert a newline`);
    await composer.fill("分析销售趋势");
    await composer.press("Enter");
    await page.getByTestId("runtime-message").waitFor();
    if (submittedMessage !== "分析销售趋势") throw new Error(`${viewport.name}: Enter did not submit the composer value`);

    const runtimeMessage = page.getByTestId("runtime-message");
    await runtimeMessage.locator("li").nth(2).waitFor();
    if (await page.locator('[aria-label="运行状态"]').count()) throw new Error(`${viewport.name}: standalone runtime status still exists`);
    const details = runtimeMessage.locator("details");
    await runtimeMessage.getByText("运行进度").click();
    if (await details.getAttribute("open") !== null) throw new Error(`${viewport.name}: runtime details did not collapse`);
    if (browserErrors.length) throw new Error(`${viewport.name}: browser errors: ${browserErrors.join(" | ")}`);

    await page.screenshot({ path: `${outputDir}/${viewport.name}.png`, fullPage: true });
    console.log(JSON.stringify({ viewport: viewport.name, submittedMessage, runtimeEvents: await runtimeMessage.locator("li").count() }));
    await page.close();
  }
} finally {
  await browser.close();
}

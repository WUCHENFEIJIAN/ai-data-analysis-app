import { describe, expect, it } from "vitest";

import { analysisEventLabel, analysisStatusLabel, appendAnalysisEvent, parseAnalysisEvent } from "./analysis-runtime";

describe("analysis runtime protocol", () => {
  it("parses persisted SSE payloads and keeps the server cursor", () => {
    const event = parseAnalysisEvent({
      data: JSON.stringify({ event: "analysis.artifact_created", run_id: "run_1", data: { path: "charts/a.png" } }),
      lastEventId: "7",
    } as MessageEvent<string>);

    expect(event).toEqual({ event: "analysis.artifact_created", run_id: "run_1", data: { path: "charts/a.png" }, sequence: 7 });
  });

  it("rejects malformed SSE data instead of changing UI state", () => {
    expect(parseAnalysisEvent({ data: "not-json", lastEventId: "1" } as MessageEvent<string>)).toBeNull();
    expect(parseAnalysisEvent({ data: JSON.stringify({ event: "analysis.status", data: {} }), lastEventId: "1" } as MessageEvent<string>)).toBeNull();
  });

  it("uses concise user-facing labels for persisted run and event states", () => {
    expect(analysisStatusLabel(null)).toBe("就绪");
    expect(analysisStatusLabel({ state: "REPORT", status: "running" } as never)).toBe("生成报告");
    expect(analysisEventLabel({ event: "analysis.execution_completed", run_id: "run_1", data: { status: "failed" }, sequence: 2 })).toContain("修复");
    expect(analysisEventLabel({ event: "analysis.retry_started", run_id: "run_1", data: {}, sequence: 3 })).toContain("继续");
    expect(analysisEventLabel({ event: "analysis.action_rejected", run_id: "run_1", data: {}, sequence: 4 })).toContain("汇总");
  });

  it("keeps every persisted task event, including events replayed after completion", () => {
    const first = { event: "analysis.plan_created", run_id: "run_1", data: {}, sequence: 1 };
    const second = { event: "analysis.execution_completed", run_id: "run_1", data: { status: "success" }, sequence: 2 };
    const completed = { event: "analysis.completed", run_id: "run_1", data: {}, sequence: 3 };

    const events = appendAnalysisEvent(appendAnalysisEvent([first], second), completed);

    expect(events).toEqual([first, second, completed]);
    expect(appendAnalysisEvent(events, second)).toBe(events);
  });
});

import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Preview, previewKindForPath } from "./preview";

afterEach(cleanup);

describe("previewKindForPath", () => {
  it.each([
    ["scripts/001.py", "text"],
    ["context/profile.json", "json"],
    ["data/output.csv", "csv"],
    ["charts/trend.png", "image"],
    ["reports/report.html", "html"],
    ["input/data.xlsx", "unknown"],
  ])("maps %s to %s", (path, expected) => {
    expect(previewKindForPath(path)).toBe(expected);
  });
});

describe("empty preview surfaces", () => {
  it.each([
    ["code", "暂无代码"],
    ["chart", "暂无图表"],
    ["data", "暂无数据"],
    ["report", "暂无报告"],
  ] as const)("keeps the %s surface selectable when it has no artifact", (activeTab, label) => {
    render(<Preview projectId="project-1" activeTab={activeTab} preview={null} loading={false} error="" />);

    expect(screen.getByText(label)).toBeTruthy();
  });
});

describe("report preview security", () => {
  it("keeps the report in an opaque-origin iframe with only scripts allowed", () => {
    render(
      <Preview
        projectId="project-1"
        preview={{ path: "reports/report.html", kind: "html", size_bytes: 10, truncated: false }}
        loading={false}
        error=""
      />,
    );

    const frame = screen.getByTitle("分析报告");
    expect(frame.getAttribute("sandbox")).toBe("allow-scripts");
    expect(frame.getAttribute("sandbox")).not.toContain("allow-same-origin");
    expect(frame.getAttribute("src")).toContain("/files/reports/report.html/content");
    expect(frame.getAttribute("src")).toContain("?v=10");
    expect(frame.getAttribute("referrerpolicy")).toBe("no-referrer");
  });

  it("changes the iframe URL when a regenerated report has a new revision", () => {
    const { rerender } = render(
      <Preview
        projectId="project-1"
        preview={{ path: "reports/report.html", kind: "html", size_bytes: 10, revision: "a", truncated: false }}
        loading={false}
        error=""
      />,
    );
    const first = screen.getByTitle("分析报告").getAttribute("src");
    rerender(
      <Preview
        projectId="project-1"
        preview={{ path: "reports/report.html", kind: "html", size_bytes: 10, revision: "b", truncated: false }}
        loading={false}
        error=""
      />,
    );
    expect(screen.getByTitle("分析报告").getAttribute("src")).not.toBe(first);
    expect(screen.getByTitle("分析报告").getAttribute("src")).toContain("?v=b");
  });

  it("shows report failure and invokes the isolated retry action", () => {
    const retry = vi.fn();
    render(
      <Preview
        projectId="project-1"
        preview={{ path: "reports/report.html", kind: "html", size_bytes: 10, truncated: false }}
        loading={false}
        error=""
        reportStatus="failed"
        reportError="生成失败"
        onRetryReport={retry}
      />,
    );

    expect(screen.queryByTitle("分析报告")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重试生成" }));
    expect(retry).toHaveBeenCalledOnce();
  });
});

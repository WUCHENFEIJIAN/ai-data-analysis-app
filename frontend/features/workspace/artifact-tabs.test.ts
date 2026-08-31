import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ArtifactTabs, artifactTabFor, latestArtifactForTab } from "./artifact-tabs";
import type { Artifact } from "../../types/api";

afterEach(cleanup);

const artifact = (path: string, artifact_type: Artifact["artifact_type"], id: string): Artifact => ({
  id,
  project_id: "project_1",
  path,
  artifact_type,
  size_bytes: 1,
  created_at: `2026-08-22T00:0${id.slice(-1)}:00Z`,
});

describe("artifact tabs", () => {
  it("maps generated artifacts to the right preview surface", () => {
    expect(artifactTabFor(artifact("scripts/001.py", "script", "1"))).toBe("code");
    expect(artifactTabFor(artifact("charts/trend.png", "chart", "2"))).toBe("chart");
    expect(artifactTabFor(artifact("data/result.csv", "data", "3"))).toBe("data");
    expect(artifactTabFor(artifact("reports/report.html", "report", "4"))).toBe("report");
  });

  it("selects the most recent artifact without mixing tab categories", () => {
    const artifacts = [artifact("charts/old.png", "chart", "1"), artifact("charts/new.png", "chart", "2"), artifact("reports/report.html", "report", "3")];
    expect(latestArtifactForTab(artifacts, "chart")?.path).toBe("charts/new.png");
    expect(latestArtifactForTab(artifacts, "data")).toBeUndefined();
  });

  it("lets the user enter an empty preview surface", () => {
    const onSelect = vi.fn();
    const view = render(React.createElement(ArtifactTabs, { artifacts: [], active: "chart", onSelect }));

    fireEvent.click(screen.getByRole("tab", { name: /代码/ }));
    expect(onSelect).toHaveBeenCalledWith("code", undefined);
    view.rerender(React.createElement(ArtifactTabs, { artifacts: [], active: "code", onSelect }));
    expect(screen.getByRole("tab", { name: /代码/ }).getAttribute("aria-selected")).toBe("true");
  });
});

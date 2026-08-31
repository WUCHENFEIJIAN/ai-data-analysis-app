import React from "react";
import { BarChart3, Code2, FileText, Table2 } from "lucide-react";

import type { Artifact } from "../../types/api";

export type ArtifactTab = "code" | "chart" | "data" | "report";

const TABS: Array<{ id: ArtifactTab; label: string; icon: typeof Code2 }> = [
  { id: "code", label: "代码", icon: Code2 },
  { id: "chart", label: "图表", icon: BarChart3 },
  { id: "data", label: "数据", icon: Table2 },
  { id: "report", label: "报告", icon: FileText },
];

export function artifactTabFor(artifact: Pick<Artifact, "artifact_type" | "path">): ArtifactTab {
  if (artifact.artifact_type === "script" || artifact.path.endsWith(".py")) return "code";
  if (artifact.artifact_type === "chart" || /\.(png|svg)$/i.test(artifact.path)) return "chart";
  if (artifact.artifact_type === "report" || artifact.path.endsWith(".html")) return "report";
  if (artifact.artifact_type === "report_spec" || artifact.path.endsWith("report_spec.json")) return "data";
  return "data";
}

export function latestArtifactForTab(artifacts: Artifact[], tab: ArtifactTab): Artifact | undefined {
  return [...artifacts].reverse().find((artifact) => artifactTabFor(artifact) === tab);
}

export function ArtifactTabs({
  artifacts,
  active,
  onSelect,
}: {
  artifacts: Artifact[];
  active: ArtifactTab;
  onSelect: (tab: ArtifactTab, artifact?: Artifact) => void;
}) {
  return (
    <div className="artifact-tabs" role="tablist" aria-label="分析产物">
      {TABS.map((tab) => {
        const artifact = latestArtifactForTab(artifacts, tab.id);
        const Icon = tab.icon;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            aria-selected={active === tab.id}
            onClick={() => onSelect(tab.id, artifact)}
            className={`artifact-tab ${active === tab.id ? "is-active" : ""}`}
          >
            <Icon size={14} />{tab.label}
          </button>
        );
      })}
    </div>
  );
}

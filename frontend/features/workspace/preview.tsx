"use client";

import React, { useState } from "react";
import dynamic from "next/dynamic";
import { Download, FileQuestion, LoaderCircle, RefreshCw, RotateCcw } from "lucide-react";

import { absoluteApiUrl, fileContentUrl, fileDownloadUrl } from "../../lib/api";
import type { FilePreview as PreviewData } from "../../types/api";
import type { ArtifactTab } from "./artifact-tabs";

export type ReportStatus = "ready" | "generating" | "failed";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), { ssr: false });
export const READ_ONLY_EDITOR_OPTIONS = {
  readOnly: true,
  domReadOnly: true,
  minimap: { enabled: false },
  lineNumbers: "on",
  scrollBeyondLastLine: false,
  wordWrap: "on",
  automaticLayout: true,
} as const;

export function previewKindForPath(path: string): PreviewData["kind"] | "unknown" {
  const extension = path.split(".").pop()?.toLowerCase();
  if (["py", "txt", "md"].includes(extension ?? "")) return "text";
  if (extension === "json") return "json";
  if (extension === "csv") return "csv";
  if (["png", "svg"].includes(extension ?? "")) return "image";
  if (extension === "html") return "html";
  return "unknown";
}

export function Preview({
  projectId,
  activeTab = "data",
  preview,
  loading,
  error,
  reportStatus = "ready",
  reportError = "",
  onRetryReport,
}: {
  projectId: string;
  activeTab?: ArtifactTab;
  preview: PreviewData | null;
  loading: boolean;
  error: string;
  reportStatus?: ReportStatus;
  reportError?: string;
  onRetryReport?: () => void;
}) {
  const [reportRevision, setReportRevision] = useState(0);
  if (loading) return <Empty text="正在加载预览" />;
  if (error) return <Empty text={error} />;
  if (!preview) {
    const emptyLabels: Record<ArtifactTab, string> = {
      code: "暂无代码",
      chart: "暂无图表",
      data: "暂无数据",
      report: "暂无报告",
    };
    return <Empty text={emptyLabels[activeTab]} />;
  }
  return (
    <div className="preview-surface">
      <div className="preview-header"><span className="preview-path">{preview.path}</span><div className="flex">{preview.kind === "html" && <button type="button" onClick={() => setReportRevision((value) => value + 1)} title="刷新报告" aria-label="刷新报告" className="tool-button"><RefreshCw size={15} /></button>}<a href={fileDownloadUrl(projectId, preview.path)} title="下载文件" aria-label="下载文件" className="tool-button"><Download size={15} /></a></div></div>
      <div className={`preview-body ${preview.kind === "html" ? "preview-body--report" : ""}`}>
        {preview.kind === "text" && preview.path.endsWith(".py") && <div className="h-full min-h-[360px]"><MonacoEditor language="python" theme="vs" value={String(preview.content ?? "")} options={READ_ONLY_EDITOR_OPTIONS} /></div>}
        {preview.kind === "text" && !preview.path.endsWith(".py") && <pre className="preview-text whitespace-pre-wrap font-mono text-xs leading-5">{String(preview.content ?? "")}</pre>}
        {preview.kind === "json" && <pre className="whitespace-pre-wrap font-mono text-xs leading-5">{JSON.stringify(preview.content, null, 2)}</pre>}
        {preview.kind === "csv" && <CsvPreview preview={preview} />}
        {preview.kind === "image" && preview.download_url && (
          // Artifact images have unknown dimensions and must be served directly from the workspace API.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={absoluteApiUrl(preview.download_url)} alt={preview.path} className="max-h-full max-w-full object-contain" />
        )}
        {preview.kind === "html" && (
          <ReportPreview
            key={`${preview.revision ?? preview.size_bytes}:${reportRevision}`}
            projectId={projectId}
            path={preview.path}
            revision={preview.revision ?? String(preview.size_bytes)}
            status={reportStatus}
            error={reportError}
            onRetry={onRetryReport}
          />
        )}
        {preview.truncated && <p className="mt-3 text-xs text-[var(--muted)]">预览已截断，下载文件可查看完整内容。</p>}
      </div>
    </div>
  );
}

export function ReportPreview({
  projectId,
  path,
  revision,
  status,
  error,
  onRetry,
}: {
  projectId: string;
  path: string;
  revision: string;
  status: ReportStatus;
  error: string;
  onRetry?: () => void;
}) {
  if (status === "generating") {
    return <div className="empty-state"><div className="empty-state__inner"><LoaderCircle className="empty-state__icon mx-auto loading-spinner" size={27} /><div className="empty-state__title">正在生成报告</div><p className="empty-state__hint">报告会在产物生成后自动刷新。</p></div></div>;
  }
  if (status === "failed") {
    return <div className="empty-state px-8 text-center"><div className="empty-state__inner"><p className="mb-3 text-sm font-semibold text-[var(--danger)]">{error || "报告生成失败"}</p>{onRetry && <button type="button" onClick={onRetry} className="primary-button"><RotateCcw size={15} />重试生成</button>}</div></div>;
  }
  return (
    <iframe
      title="分析报告"
      src={`${fileContentUrl(projectId, path)}?v=${encodeURIComponent(revision)}`}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
      className="h-full min-h-[360px] w-full border-0 bg-white"
    />
  );
}

function CsvPreview({ preview }: { preview: PreviewData }) {
  return <div className="data-table"><table><thead><tr>{preview.columns?.map((column, index) => <th key={`${column}-${index}`}>{column}</th>)}</tr></thead><tbody>{preview.rows?.map((row, rowIndex) => <tr key={rowIndex}>{row.map((value, index) => <td key={index} className="truncate">{value}</td>)}</tr>)}</tbody></table></div>;
}

function Empty({ text }: { text: string }) {
  return <div className="empty-state"><div className="empty-state__inner"><FileQuestion className="empty-state__icon" size={27} /><div className="empty-state__title">{text}</div><p className="empty-state__hint">选择左侧文件或切换上方产物标签即可查看内容。</p></div></div>;
}

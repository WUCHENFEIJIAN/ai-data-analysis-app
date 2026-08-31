"use client";

import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { ArrowLeft, Database, LoaderCircle, RefreshCw, Sparkles, Upload } from "lucide-react";

import { getProject, listArtifacts, listFiles, listMessages, previewFile, uploadFile } from "@/lib/api";
import type { Artifact, FileNode, FilePreview, Message, Project } from "@/types/api";
import { FileTree } from "./file-tree";
import { Preview } from "./preview";
import { analysisErrorMessage, ChatPanel } from "./chat-panel";
import { ArtifactTabs, artifactTabFor, latestArtifactForTab, type ArtifactTab } from "./artifact-tabs";
import { useAnalysisRun } from "./analysis-runtime";
import { SettingsButton } from "../settings/settings-dialog";

export function ProjectWorkspace({ projectId }: { projectId: string }) {
  const fileInput = useRef<HTMLInputElement>(null);
  const hasSelectedRef = useRef(false);
  const [project, setProject] = useState<Project | null>(null);
  const [files, setFiles] = useState<FileNode[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [selected, setSelected] = useState<FileNode | null>(null);
  const [preview, setPreview] = useState<FilePreview | null>(null);
  const [activeTab, setActiveTab] = useState<ArtifactTab>("data");
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [messagesLoading, setMessagesLoading] = useState(true);
  const [messagesError, setMessagesError] = useState("");
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [profileNotice, setProfileNotice] = useState("");
  const [error, setError] = useState("");
  const [isMobile, setIsMobile] = useState(false);
  const [isResizing, setIsResizing] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 700px)");
    const update = () => setIsMobile(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  const selectPath = useCallback(async (path: string) => {
    hasSelectedRef.current = true;
    setSelected({ name: path.split("/").pop() ?? path, path, kind: "file" });
    setActiveTab(artifactTabFor({ path, artifact_type: "file" }));
    setLoadingPreview(true); setError("");
    try { setPreview(await previewFile(projectId, path)); } catch (caught) { setPreview(null); setError(caught instanceof Error ? caught.message : "无法预览文件"); } finally { setLoadingPreview(false); }
  }, [projectId]);

  const refresh = useCallback(async (preferredPath?: string) => {
    try {
      const [nextFiles, nextArtifacts] = await Promise.all([listFiles(projectId), listArtifacts(projectId)]);
      setFiles(nextFiles); setArtifacts(nextArtifacts);
      const fallback = latestArtifactForTab(nextArtifacts, "report") ?? nextArtifacts.at(-1);
      const target = preferredPath || (!hasSelectedRef.current ? fallback?.path : undefined);
      if (target) await selectPath(target);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "无法加载文件"); }
  }, [projectId, selectPath]);

  const loadMessages = useCallback(async () => {
    setMessagesError("");
    try { setMessages(await listMessages(projectId)); } catch (caught) { setMessagesError(caught instanceof Error ? caught.message : "无法加载对话"); } finally { setMessagesLoading(false); }
  }, [projectId]);

  const handleArtifactCreated = useCallback(async (path: string) => {
    await refresh(path);
  }, [refresh]);

  const runtimeOptions = useMemo(() => ({ onConversationChanged: loadMessages, onArtifactCreated: handleArtifactCreated }), [handleArtifactCreated, loadMessages]);
  const runtime = useAnalysisRun(projectId, runtimeOptions);

  useEffect(() => {
    void Promise.all([getProject(projectId).then(setProject), refresh(), loadMessages()]).catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "无法加载项目"));
  }, [projectId, refresh, loadMessages]);

  async function selectFile(node: FileNode) {
    await selectPath(node.path);
  }

  async function handleUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploadProgress(0); setError(""); setProfileNotice("");
    try {
      const uploaded = await uploadFile(projectId, file, setUploadProgress);
      setProfileNotice(uploaded.profile_status === "failed" ? uploaded.profile_error ?? "数据概要生成失败" : "数据概要已生成");
      await refresh();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "上传失败"); } finally { setUploadProgress(null); event.target.value = ""; }
  }

  return (
    <main className="studio-shell flex h-screen min-h-[560px] flex-col overflow-hidden">
      <header className="studio-topbar shrink-0">
        <div className="studio-topbar__brand">
          <Link href="/" title="返回项目列表" className="tool-button" aria-label="返回项目列表"><ArrowLeft size={16} /></Link>
          <span className="studio-brand-mark"><Sparkles size={16} /></span>
          <div className="min-w-0"><div className="studio-topbar__title">{project?.name ?? "加载项目"}</div><div className="studio-topbar__meta">Data Studio / analysis workspace</div></div>
        </div>
        <div className="flex items-center gap-3">
          <span className="studio-topbar__status">分析工作台</span>
          <span className="hidden font-mono text-[10px] text-[var(--muted)] sm:inline">{projectId}</span><SettingsButton />
        </div>
      </header>
      <PanelGroup direction={isMobile ? "vertical" : "horizontal"} className={`workspace-grid${isResizing ? " is-resizing" : ""}`}>
        <Panel defaultSize={20} minSize={15} maxSize={32}>
          <aside className="studio-panel studio-panel--files h-full">
            <div className="panel-header">
              <div className="panel-heading"><Database size={15} className="text-[var(--accent)]" /><div><div className="panel-kicker">Source library</div><div className="panel-title">文件</div></div></div>
              <div className="flex gap-1"><button type="button" onClick={() => void refresh()} title="刷新文件" aria-label="刷新文件" className="tool-button"><RefreshCw size={14} /></button><button type="button" onClick={() => fileInput.current?.click()} title="上传数据" aria-label="上传数据" className="tool-button"><Upload size={14} /></button></div>
            </div>
            <input ref={fileInput} type="file" accept=".csv,.xlsx,.xls" onChange={(event) => void handleUpload(event)} className="hidden" />
            {uploadProgress !== null && <div className="upload-progress"><div className="upload-progress__meta"><span>正在导入数据</span><span>{uploadProgress}%</span></div><div className="upload-progress__track"><div className="upload-progress__bar" style={{ width: `${uploadProgress}%` }} /></div></div>}
            {profileNotice && <p className="border-b border-[var(--line)] bg-[var(--surface-muted)] px-4 py-2.5 text-[10px] text-[var(--muted)]">{profileNotice}</p>}
            <div className="file-tree-scroll">{files.length ? <FileTree nodes={files} selected={selected?.path} onSelect={(node) => void selectFile(node)} /> : <div className="empty-state"><div className="empty-state__inner"><Database className="empty-state__icon mx-auto" size={24} /><div className="empty-state__title">等待数据源</div><p className="empty-state__hint">上传 Excel 或 CSV，项目文件会出现在这里。</p></div></div>}</div>
          </aside>
        </Panel>
        <PanelResizeHandle onDragging={setIsResizing} className={isMobile ? "resize-handle h-2" : "resize-handle w-2"} />
        <Panel defaultSize={37} minSize={26}>
          <ChatPanel messages={messages} messagesLoading={messagesLoading} messagesError={messagesError} runtime={runtime} />
        </Panel>
        <PanelResizeHandle onDragging={setIsResizing} className={isMobile ? "resize-handle h-2" : "resize-handle w-2"} />
        <Panel defaultSize={43} minSize={30}>
          <section className="studio-panel studio-panel--preview h-full"><ArtifactTabs artifacts={artifacts} active={activeTab} onSelect={(tab, artifact) => { setActiveTab(tab); setError(""); if (artifact) void selectPath(artifact.path); else { hasSelectedRef.current = true; setSelected(null); setPreview(null); setLoadingPreview(false); } }} />{!project && !error ? <div className="grid h-full place-items-center"><LoaderCircle className="loading-spinner preview-loading" size={20} /></div> : <Preview projectId={projectId} activeTab={activeTab} preview={preview} loading={loadingPreview} error={error} reportStatus={runtime.reportStatus} reportError={runtime.run?.state === "REPORT" ? analysisErrorMessage(runtime.error, "", runtime.run) : ""} onRetryReport={() => void runtime.retryReport()} />}</section>
        </Panel>
      </PanelGroup>
    </main>
  );
}

"use client";

import React, { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, FolderKanban, LoaderCircle, Plus, Trash2 } from "lucide-react";

import { createProject, deleteProject, listProjects } from "../../lib/api";
import type { Project } from "../../types/api";
import { SettingsButton } from "../settings/settings-dialog";

export function ProjectList() {
  const nameInput = useRef<HTMLInputElement>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [name, setName] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setProjects((await listProjects()).items);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "无法加载项目");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => void load(), [load]);

  async function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!name.trim()) {
      setError("请先输入项目名称");
      nameInput.current?.focus();
      return;
    }
    setSaving(true);
    setError("");
    try {
      const project = await createProject(name);
      setProjects((current) => [project, ...current]);
      setName("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "创建失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleDelete(project: Project) {
    if (!window.confirm(`删除“${project.name}”及其全部分析文件？`)) return;
    try {
      await deleteProject(project.id);
      setProjects((current) => current.filter((item) => item.id !== project.id));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "删除失败");
    }
  }

  return (
    <main className="landing-shell">
      <header className="studio-topbar">
        <div className="studio-topbar__brand">
          <span className="studio-brand-mark"><FolderKanban size={17} /></span>
          <div><div className="studio-topbar__title">Data Studio</div><div className="studio-topbar__meta">分析项目</div></div>
        </div>
        <div className="flex items-center gap-2"><span className="studio-topbar__status">{projects.length} 个项目</span><SettingsButton /></div>
      </header>
      <div className="landing-content">
        <div className="landing-intro">
          <div>
            <div className="landing-eyebrow">Signal Desk / workspace index</div>
            <h1 className="landing-title">把数据变成<br />下一步行动。</h1>
            <p className="landing-copy">管理分析项目、上传数据，并在同一个工作台里追踪计算、图表和报告产物。</p>
          </div>
          <div className="create-card">
            <div className="create-card__label">开始一个新的分析项目</div>
            <form onSubmit={handleCreate} className="create-card__form">
              <label className="sr-only" htmlFor="project-name">项目名称</label>
              <input ref={nameInput} id="project-name" value={name} onChange={(event) => { setName(event.target.value); if (error === "请先输入项目名称") setError(""); }} placeholder="例如：Q3 销售复盘" maxLength={120} className="create-card__input" />
              <button type="submit" disabled={saving} className="primary-button">{saving ? <LoaderCircle className="loading-spinner" size={15} /> : <Plus size={15} />}新建项目</button>
            </form>
          </div>
        </div>
        {error && <p role="alert" className="project-list__error">{error}</p>}
        {loading ? (
          <div className="empty-state min-h-[260px]"><div className="empty-state__inner"><LoaderCircle className="empty-state__icon mx-auto loading-spinner" size={28} /><div className="empty-state__title">正在加载项目</div></div></div>
        ) : projects.length === 0 ? (
          <div className="empty-state empty-project-state min-h-[260px]"><div className="empty-state__inner"><FolderKanban className="empty-state__icon mx-auto" size={28} /><div className="empty-state__title">还没有分析项目</div><p className="empty-state__hint">创建项目后上传 Excel 或 CSV 开始分析。</p></div></div>
        ) : (
          <section>
            <div className="project-section__header"><div className="project-section__title">你的分析项目</div><div className="project-section__count">按最近更新排序</div></div>
            <div className="project-grid">
            {projects.map((project) => (
              <article key={project.id} className="project-card">
                <div>
                  <div className="project-card__top"><span className="project-card__icon"><FolderKanban size={17} /></span><span className="project-card__tag">Project</span></div>
                  <h2 className="project-card__name">{project.name}</h2>
                  <p className="project-card__meta">更新于 {new Date(project.updated_at).toLocaleString("zh-CN")}</p>
                </div>
                <div className="project-card__actions">
                  <button type="button" onClick={() => void handleDelete(project)} title="删除项目" aria-label={`删除项目 ${project.name}`} className="delete-button"><Trash2 size={15} /></button>
                  <Link href={`/projects/${project.id}`} className="project-card__open">打开工作台<ArrowRight size={14} /></Link>
                </div>
              </article>
            ))}
            </div>
          </section>
        )}
      </div>
    </main>
  );
}

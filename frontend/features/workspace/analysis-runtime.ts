"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  analysisEventsUrl,
  getAnalysis,
  getLatestAnalysis,
  regenerateReport,
  retryAnalysis,
  resumeAnalysis,
  startAnalysis,
  stopAnalysis,
} from "../../lib/api";
import type { AnalysisEvent, AnalysisRun } from "../../types/api";

export const ANALYSIS_EVENT_TYPES = [
  "analysis.started",
  "analysis.status",
  "analysis.ask_user",
  "analysis.plan_created",
  "analysis.code_generated",
  "analysis.execution_started",
  "analysis.execution_completed",
  "analysis.artifact_created",
  "analysis.report_started",
  "analysis.report_completed",
  "analysis.completed",
  "analysis.failed",
  "analysis.retry_started",
  "analysis.action_rejected",
  "analysis.stopped",
] as const;

const ACTIVE_STATUSES = new Set(["pending", "running"]);
const TERMINAL_STATUSES = new Set(["completed", "failed", "stopped"]);

export function parseAnalysisEvent(event: MessageEvent<string>): AnalysisEvent | null {
  try {
    const payload = JSON.parse(event.data) as Omit<AnalysisEvent, "sequence">;
    if (!payload.event || !payload.run_id || typeof payload.data !== "object") return null;
    return { ...payload, sequence: Number(event.lastEventId || 0) };
  } catch {
    return null;
  }
}

export function appendAnalysisEvent(current: AnalysisEvent[], event: AnalysisEvent): AnalysisEvent[] {
  if (current.some((item) => item.sequence === event.sequence && item.run_id === event.run_id)) return current;
  return [...current, event];
}

export function analysisStatusLabel(run: AnalysisRun | null): string {
  if (!run) return "就绪";
  if (run.status === "waiting_user") return "等待补充信息";
  if (run.status === "completed") return "分析完成";
  if (run.status === "failed") return "运行失败";
  if (run.status === "stopped") return "已停止";
  const states: Record<string, string> = {
    UNDERSTAND: "理解需求",
    CLARIFY: "整理补充信息",
    PLAN: "制定计划",
    ANALYZE: "执行分析",
    EVALUATE: "评估结果",
    REPORT: "生成报告",
  };
  return states[run.state] ?? "分析运行中";
}

export function analysisEventLabel(event: AnalysisEvent): string {
  if (event.event === "analysis.started") return "分析已开始";
  if (event.event === "analysis.ask_user") return "需要补充信息";
  if (event.event === "analysis.plan_created") return "分析计划已生成";
  if (event.event === "analysis.code_generated") return "分析脚本已生成";
  if (event.event === "analysis.execution_started") return "正在执行分析脚本";
  if (event.event === "analysis.execution_completed") {
    return event.data.status === "success" ? "分析脚本执行完成" : "执行失败，正在评估修复";
  }
  if (event.event === "analysis.artifact_created") return "新的分析产物已生成";
  if (event.event === "analysis.report_started") return "正在生成分析报告";
  if (event.event === "analysis.report_completed") return "分析报告已生成";
  if (event.event === "analysis.completed") return "分析全部完成";
  if (event.event === "analysis.failed") return "分析运行失败";
  if (event.event === "analysis.retry_started") return "正在从失败步骤继续";
  if (event.event === "analysis.action_rejected") return "报告前正在汇总分析结论";
  if (event.event === "analysis.stopped") return "分析已停止";
  const state = typeof event.data.state === "string" ? event.data.state : "";
  return state ? analysisStatusLabel({ state, status: "running" } as AnalysisRun) : "分析状态已更新";
}

interface AnalysisRuntimeOptions {
  onConversationChanged: () => Promise<void>;
  onArtifactCreated: (path: string) => Promise<void>;
}

export function useAnalysisRun(projectId: string, options: AnalysisRuntimeOptions) {
  const [run, setRun] = useState<AnalysisRun | null>(null);
  const [events, setEvents] = useState<AnalysisEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    const latest = await getLatestAnalysis(projectId);
    setRun(latest);
    return latest;
  }, [projectId]);

  useEffect(() => {
    setLoading(true);
    void refresh()
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "无法恢复分析状态"))
      .finally(() => setLoading(false));
  }, [refresh]);

  useEffect(() => {
    const runId = run?.id;
    const runStatus = run?.status;
    if (!runId || !runStatus || (!ACTIVE_STATUSES.has(runStatus) && !TERMINAL_STATUSES.has(runStatus))) return;
    const source = new EventSource(analysisEventsUrl(runId));

    const handleEvent = (raw: Event) => {
      const event = parseAnalysisEvent(raw as MessageEvent<string>);
      if (!event) return;
      setEvents((current) => appendAnalysisEvent(current, event));
      if (event.event === "analysis.artifact_created" || event.event === "analysis.report_completed") {
        const path = typeof event.data.path === "string" ? event.data.path : "";
        if (path) void options.onArtifactCreated(path);
      }
      if (["analysis.ask_user", "analysis.plan_created", "analysis.completed", "analysis.failed"].includes(event.event)) {
        void options.onConversationChanged();
      }
      void getAnalysis(runId).then((latest) => {
        setRun(latest);
        if (!ACTIVE_STATUSES.has(latest.status)) source.close();
      }).catch(() => undefined);
    };

    ANALYSIS_EVENT_TYPES.forEach((eventType) => source.addEventListener(eventType, handleEvent));
    source.onerror = () => {
      void getAnalysis(runId)
        .then((latest) => {
          setRun(latest);
          if (!ACTIVE_STATUSES.has(latest.status)) source.close();
        })
        .catch(() => setError("运行状态连接中断，正在等待安全重连"));
    };
    return () => source.close();
  }, [run?.id, run?.status, options]);

  useEffect(() => {
    const runId = run?.id;
    const runStatus = run?.status;
    if (!runId || !runStatus || !ACTIVE_STATUSES.has(runStatus)) return;
    const timer = window.setInterval(() => {
      void getAnalysis(runId)
        .then((latest) => setRun(latest))
        .catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [run?.id, run?.status]);

  const perform = useCallback(async <T,>(operation: () => Promise<T>): Promise<T> => {
    setBusy(true);
    setError("");
    try {
      return await operation();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "操作失败");
      throw caught;
    } finally {
      setBusy(false);
    }
  }, []);

  const start = useCallback(async (message: string) => perform(async () => {
    setEvents([]);
    const created = await startAnalysis(projectId, message);
    setRun(created);
    await options.onConversationChanged();
    return created;
  }), [options, perform, projectId]);

  const resume = useCallback(async (message: string) => {
    if (!run) return null;
    return perform(async () => {
      const resumed = await resumeAnalysis(run.id, message);
      setRun(resumed);
      await options.onConversationChanged();
      return resumed;
    });
  }, [options, perform, run]);

  const stop = useCallback(async () => {
    if (!run) return null;
    return perform(async () => {
      const stopped = await stopAnalysis(run.id);
      setRun(stopped);
      return stopped;
    });
  }, [perform, run]);

  const retryReport = useCallback(async () => {
    if (!run) return null;
    return perform(async () => {
      setRun((current) => current ? { ...current, status: "running", state: "REPORT", error_message: null } : current);
      const started = await regenerateReport(run.id);
      setRun(started);
      return started;
    });
  }, [perform, run]);

  const retry = useCallback(async () => {
    if (!run) return null;
    return perform(async () => {
      setEvents([]);
      const retried = await retryAnalysis(run.id);
      setRun(retried);
      return retried;
    });
  }, [perform, run]);

  const reportStatus = useMemo(() => {
    if (run?.state === "REPORT" && run.status === "failed") return "failed" as const;
    if (run?.state === "REPORT" && ["pending", "running"].includes(run.status)) return "generating" as const;
    return "ready" as const;
  }, [run]);

  return {
    run,
    events,
    loading,
    busy,
    error,
    reportStatus,
    refresh,
    start,
    resume,
    stop,
    retry,
    retryReport,
    clearError: () => setError(""),
  };
}

export type AnalysisRuntime = ReturnType<typeof useAnalysisRun>;

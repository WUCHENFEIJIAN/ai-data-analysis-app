"use client";

import React, { FormEvent, KeyboardEvent, useState } from "react";
import { AlertCircle, Bot, Check, ChevronRight, LoaderCircle, MessageCircle, RotateCcw, Send, Square, User } from "lucide-react";

import type { AnalysisEvent, AnalysisRun, Message } from "../../types/api";
import { analysisEventLabel, analysisStatusLabel, type AnalysisRuntime } from "./analysis-runtime";

export const WELCOME_MESSAGE: Message = {
  id: "workspace-welcome",
  role: "assistant",
  message_type: "text",
  created_at: "",
  content: "你好，我是你的数据分析助手。上传 Excel 或 CSV 并告诉我想了解的问题，我会分析数据并生成图表和报告。",
};

export function visibleChatMessages(messages: Message[]): Message[] {
  return messages.filter((message) => message.message_type !== "plan");
}

export function chatMessagesWithWelcome(messages: Message[]): Message[] {
  return [WELCOME_MESSAGE, ...visibleChatMessages(messages).filter((message) => message.id !== WELCOME_MESSAGE.id)];
}

const REPORT_STAGE_HINTS: Record<string, string> = {
  "Report editor returned invalid output": "报告编辑失败，请重试生成报告",
  "Report references a missing claim or insight": "报告引用了不存在的结论或证据",
  "Report presentation preflight failed": "报告预检查失败",
  "Report rendering failed": "报告渲染失败",
  "Report publishing failed": "报告发布失败",
};

export function analysisErrorMessage(
  runtimeError: string,
  messagesError: string,
  run: AnalysisRun | null,
): string {
  const raw = runtimeError || messagesError || (run?.status === "failed" ? run.error_message ?? "" : "");
  return REPORT_STAGE_HINTS[raw] ?? raw;
}

export function analysisEventStatus(event: AnalysisEvent, isLatest: boolean, run: AnalysisRun | null): string {
  if (event.event === "analysis.failed") return "失败";
  if (event.event === "analysis.stopped") return "已停止";
  if (event.event === "analysis.ask_user") return "等待补充";
  if (event.event === "analysis.execution_completed" && event.data.status !== "success") return "需要修复";
  if (isLatest) {
    if (run?.status === "failed") return "失败";
    if (run?.status === "stopped") return "已停止";
    if (run?.status === "waiting_user") return "等待补充";
    if (run?.status === "pending" || run?.status === "running") return "进行中";
  }
  return "已完成";
}

export function ChatPanel({
  messages,
  messagesLoading,
  messagesError,
  runtime,
}: {
  messages: Message[];
  messagesLoading: boolean;
  messagesError: string;
  runtime: AnalysisRuntime;
}) {
  const [input, setInput] = useState("");
  const canStop = runtime.run?.status === "pending" || runtime.run?.status === "running";
  const waitingForUser = runtime.run?.status === "waiting_user";
  const displayError = analysisErrorMessage(runtime.error, messagesError, runtime.run);
  const displayMessages = chatMessagesWithWelcome(messages);
  const completionSummary = runtime.run?.status === "completed"
    ? [...displayMessages].reverse().find((message) => message.role === "assistant" && message.message_type === "result")
    : undefined;
  const transcriptMessages = displayMessages.filter((message) => message.id !== completionSummary?.id);

  async function submit(message: string) {
    if (!message.trim() || runtime.busy) return;
    try {
      if (waitingForUser) await runtime.resume(message);
      else await runtime.start(message);
      setInput("");
    } catch {
      // The runtime exposes the normalized API error and keeps the request available for retry.
    }
  }

  function handleSubmit(event: FormEvent) { event.preventDefault(); void submit(input); }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    void submit(input);
  }

  return (
    <section className="studio-panel studio-panel--chat h-full">
      <div className="panel-header chat-panel__header">
        <div className="panel-heading"><span className="chat-panel__heading-icon"><MessageCircle size={15} /></span><div><div className="panel-kicker">Analysis console</div><div className="panel-title">分析对话</div><div className="chat-panel__subline">基于真实产物的分析工作流</div></div></div>
        <div className="chat-panel__header-actions"><span className="panel-status">{runtime.loading ? "恢复状态中" : analysisStatusLabel(runtime.run)}</span>{canStop && <button type="button" onClick={() => void runtime.stop()} disabled={runtime.busy} title="停止分析" aria-label="停止分析" className="tool-button chat-panel__stop"><Square size={12} fill="currentColor" /></button>}</div>
      </div>
      <div className="chat-scroll">
        {messagesLoading ? <div className="grid h-full place-items-center"><LoaderCircle className="chat-panel__loading loading-spinner" size={20} /></div> : <div aria-label="对话消息" className="space-y-4">{transcriptMessages.map((message) => <MessageRow key={message.id} message={message} />)}{!waitingForUser && <RuntimeMessage runtime={runtime} summary={completionSummary?.content} />}</div>}
      </div>
      {displayError && <div role="alert" className="chat-panel__error"><span className="inline-flex items-center gap-1.5"><AlertCircle size={14} />{displayError}</span><button type="button" disabled={runtime.busy} onClick={() => void (runtime.run?.state === "REPORT" ? runtime.retryReport() : runtime.retry())} className="inline-flex shrink-0 items-center gap-1 font-semibold disabled:opacity-40"><RotateCcw size={13} />{runtime.run?.state === "REPORT" ? "重试报告" : "从失败处继续"}</button></div>}
      <form onSubmit={handleSubmit} className="composer"><div className="composer__hint">{waitingForUser ? "补充信息后继续当前分析" : "Enter 发送 · Shift + Enter 换行"}</div><div className="composer__inner"><label htmlFor="analysis-request" className="sr-only">分析需求</label><textarea id="analysis-request" value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={handleKeyDown} placeholder={waitingForUser ? "补充说明后继续同一次分析" : "例如：比较各渠道销售趋势，并找出主要增长来源"} className="composer__textarea max-h-32 min-h-16 min-w-0 w-full resize-none border-0 bg-transparent text-sm leading-6 outline-none" /><button type="submit" disabled={runtime.busy || canStop || !input.trim()} aria-label={waitingForUser ? "继续分析" : "发送"} title={waitingForUser ? "继续分析" : "发送"} className="composer__send">{runtime.busy ? <LoaderCircle className="loading-spinner" size={15} /> : <Send size={15} />}</button></div></form>
    </section>
  );
}

function RuntimeMessage({ runtime, summary }: { runtime: AnalysisRuntime; summary?: string }) {
  if (!runtime.run && runtime.events.length === 0) return null;
  const overallStatus = analysisStatusLabel(runtime.run);
  const active = runtime.run?.status === "pending" || runtime.run?.status === "running";
  return <div data-testid="runtime-message" className="flex justify-start gap-2.5"><span className="chat-avatar chat-avatar--assistant"><Bot size={14} /></span><div className="runtime-card w-full max-w-[82%] text-sm">
    {active && <div data-testid="analysis-intro" className="runtime-intro flex items-center gap-2"><LoaderCircle className="shrink-0 loading-spinner" size={14} /><span>好的，正在分析，请耐心等待</span></div>}
    <details><summary className="runtime-summary"><ChevronRight className="runtime-summary__icon shrink-0" size={14} /><span className="flex-1">运行进度</span><span className="runtime-summary__status">{overallStatus}</span></summary><ol className="runtime-list space-y-2">{runtime.events.length ? runtime.events.map((event, index) => { const status = analysisEventStatus(event, index === runtime.events.length - 1, runtime.run); const inProgress = status === "进行中"; return <li key={`${event.run_id}-${event.sequence}`} className="flex items-start gap-2 text-xs leading-5">{inProgress ? <LoaderCircle data-testid="progress-spinner" size={13} className="runtime-event__spinner mt-0.5 shrink-0 loading-spinner" /> : <Check size={13} className={`runtime-event__check mt-0.5 shrink-0 ${status === "失败" || status === "需要修复" ? "is-error" : ""}`} />}<span className="min-w-0 flex-1">{analysisEventLabel(event)}</span><span className="runtime-event__status shrink-0">{status}</span></li>; }) : <li className="flex items-center justify-between gap-2 text-xs"><span>{overallStatus}</span><span className="runtime-event__status shrink-0">{active ? "进行中" : "已完成"}</span></li>}</ol></details>
    {summary && <div data-testid="analysis-summary" className="runtime-summary-text">{summary}</div>}
  </div></div>;
}

function MessageRow({ message }: { message: Message }) {
  const isUser = message.role === "user";
  return <div className={`chat-message ${isUser ? "chat-message--user" : "chat-message--assistant"}`}>{!isUser && <span className="chat-avatar chat-avatar--assistant"><Bot size={14} /></span>}<div className={`${isUser ? "user-bubble" : "assistant-bubble"} max-w-[82%] px-3 py-2 text-sm leading-6`}>{message.content}</div>{isUser && <span className="chat-avatar chat-avatar--user"><User size={14} /></span>}</div>;
}

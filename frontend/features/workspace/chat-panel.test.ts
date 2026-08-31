import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Message } from "../../types/api";
import type { AnalysisRuntime } from "./analysis-runtime";
import { analysisErrorMessage, analysisEventStatus, ChatPanel, chatMessagesWithWelcome, WELCOME_MESSAGE, visibleChatMessages } from "./chat-panel";

afterEach(cleanup);

function runtime(overrides: Partial<AnalysisRuntime> = {}): AnalysisRuntime {
  return {
    run: null,
    events: [],
    loading: false,
    busy: false,
    error: "",
    reportStatus: "ready",
    refresh: vi.fn(),
    start: vi.fn().mockResolvedValue(null),
    resume: vi.fn().mockResolvedValue(null),
    stop: vi.fn(),
    retry: vi.fn(),
    retryReport: vi.fn(),
    clearError: vi.fn(),
    ...overrides,
  } as AnalysisRuntime;
}

function renderChat(currentRuntime: AnalysisRuntime) {
  return render(React.createElement(ChatPanel, {
    messages: [],
    messagesLoading: false,
    messagesError: "",
    runtime: currentRuntime,
  }));
}

describe("visibleChatMessages", () => {
  it("does not expose persisted analysis plans in the chat transcript", () => {
    const message: Message = {
      id: "msg_1",
      role: "assistant",
      message_type: "plan",
      created_at: "2026-08-22T00:00:00Z",
      content: JSON.stringify({ action: "create_plan", title: "Plan", objective: "Goal", tasks: [] }),
    };
    const visible = visibleChatMessages([
      message,
      { ...message, id: "msg_2", message_type: "result", content: "分析完成" },
    ]);
    expect(visible.map((item) => item.content)).toEqual(["分析完成"]);
  });
});

describe("workspace welcome message", () => {
  it("starts an empty project conversation with an assistant welcome message", () => {
    renderChat(runtime());

    expect(screen.getByText(WELCOME_MESSAGE.content)).toBeTruthy();
    expect(WELCOME_MESSAGE.content.split(/[。！？]/).filter(Boolean)).toHaveLength(2);
  });

  it("keeps the welcome message first without hiding conversation history", () => {
    const history: Message[] = [{
      id: "msg_user",
      role: "user",
      message_type: "text",
      created_at: "2026-08-24T00:00:00Z",
      content: "分析销售趋势",
    }];

    expect(chatMessagesWithWelcome(history).map((message) => message.id)).toEqual([
      WELCOME_MESSAGE.id,
      "msg_user",
    ]);
  });
});

describe("analysisErrorMessage", () => {
  it("shows the persisted backend error for a failed run", () => {
    expect(analysisErrorMessage("", "", {
      status: "failed",
      error_message: "Model service authentication failed",
    } as never)).toBe("Model service authentication failed");
  });

  it("shows a stage-specific hint for report failures", () => {
    expect(analysisErrorMessage("", "", {
      status: "failed",
      state: "REPORT",
      error_message: "Report rendering failed",
    } as never)).toBe("报告渲染失败");
  });

  it("prefers an immediate request error and hides stale run errors", () => {
    expect(analysisErrorMessage("Request failed", "", {
      status: "failed",
      error_message: "Old error",
    } as never)).toBe("Request failed");
    expect(analysisErrorMessage("", "", {
      status: "running",
      error_message: "Old error",
    } as never)).toBe("");
  });
});

describe("chat composer keyboard controls", () => {
  it("sends with Enter and keeps the send button inside the composer at the right", async () => {
    const currentRuntime = runtime();
    renderChat(currentRuntime);
    const input = screen.getByLabelText("分析需求");
    const send = screen.getByRole("button", { name: "发送" });

    fireEvent.change(input, { target: { value: "分析销售趋势" } });
    expect(input.classList.contains("composer__textarea")).toBe(true);
    expect(send.classList.contains("composer__send")).toBe(true);
    expect(send.parentElement?.lastElementChild).toBe(send);
    expect(fireEvent.keyDown(input, { key: "Enter", code: "Enter" })).toBe(false);

    await waitFor(() => expect(currentRuntime.start).toHaveBeenCalledWith("分析销售趋势"));
  });

  it("keeps Shift+Enter for a newline without sending", () => {
    const currentRuntime = runtime();
    renderChat(currentRuntime);
    const input = screen.getByLabelText("分析需求");

    fireEvent.change(input, { target: { value: "第一行" } });
    expect(fireEvent.keyDown(input, { key: "Enter", code: "Enter", shiftKey: true })).toBe(true);
    expect(currentRuntime.start).not.toHaveBeenCalled();
  });

  it("does not send while an input method is composing text", () => {
    const currentRuntime = runtime();
    renderChat(currentRuntime);
    const input = screen.getByLabelText("分析需求");

    fireEvent.change(input, { target: { value: "销售" } });
    fireEvent.keyDown(input, { key: "Enter", code: "Enter", isComposing: true });
    expect(currentRuntime.start).not.toHaveBeenCalled();
  });
});

describe("analysis runtime message", () => {
  const events = [
    { event: "analysis.started", run_id: "run_1", sequence: 1, data: {} },
    { event: "analysis.plan_created", run_id: "run_1", sequence: 2, data: {} },
    { event: "analysis.execution_started", run_id: "run_1", sequence: 3, data: {} },
  ];

  it("renders all current events in one collapsible assistant message", () => {
    renderChat(runtime({
      run: { id: "run_1", status: "running", state: "ANALYZE" } as never,
      events,
    }));

    const message = screen.getByTestId("runtime-message");
    expect(screen.getByLabelText("对话消息").contains(message)).toBe(true);
    expect(message.querySelectorAll("li")).toHaveLength(3);
    expect(message.textContent).toContain("分析已开始");
    expect(message.textContent).toContain("分析计划已生成");
    expect(message.textContent).toContain("正在执行分析脚本");

    const details = message.querySelector("details");
    expect(screen.getByTestId("analysis-intro").textContent).toContain("好的，正在分析，请耐心等待");
    expect(screen.getByTestId("analysis-intro").querySelector("svg")?.classList.contains("loading-spinner")).toBe(true);
    expect(screen.getByTestId("progress-spinner")).toBeTruthy();
    expect(screen.getByTestId("progress-spinner").classList.contains("loading-spinner")).toBe(true);
    expect(details?.open).toBe(false);
    fireEvent.click(screen.getByText("运行进度"));
    expect(details?.open).toBe(true);
  });

  it("hides runtime progress while the assistant is waiting for the user", () => {
    renderChat(runtime({
      run: { id: "run_1", status: "waiting_user", state: "CLARIFY" } as never,
      events,
    }));

    expect(screen.queryByTestId("runtime-message")).toBeNull();
  });

  it("merges the completed analysis summary into the runtime message", () => {
    const summary: Message = {
      id: "result_1",
      role: "assistant",
      message_type: "result",
      created_at: "2026-08-25T00:00:00Z",
      content: "分析完成：销售额整体增长。",
    };
    render(React.createElement(ChatPanel, {
      messages: [summary],
      messagesLoading: false,
      messagesError: "",
      runtime: runtime({
        run: { id: "run_1", status: "completed", state: "REPORT" } as never,
        events,
      }),
    }));

    expect(screen.getByTestId("runtime-message").contains(screen.getByTestId("analysis-summary"))).toBe(true);
    expect(screen.getByTestId("analysis-summary").textContent).toContain("销售额整体增长");
    expect(screen.queryByText(summary.content)).toBe(screen.getByTestId("analysis-summary"));
  });

  it("marks earlier events complete and the latest active event in progress", () => {
    const run = { status: "running", state: "ANALYZE" } as never;
    expect(analysisEventStatus(events[0], false, run)).toBe("已完成");
    expect(analysisEventStatus(events[2], true, run)).toBe("进行中");
  });
});

import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { getModelConfiguration, listModelPresets, testModelConnection } from "../../lib/api";
import { SettingsButton } from "./settings-dialog";
import { ThemeProvider } from "./theme-provider";

vi.mock("../../lib/api", () => ({
  getModelConfiguration: vi.fn().mockResolvedValue(null),
  listModelPresets: vi.fn().mockResolvedValue({ items: [] }),
  testModelConnection: vi.fn().mockResolvedValue({
    ok: true,
    provider: "openai_compatible",
    model: "deepseek-chat",
    latency_ms: 42,
    message: "连接成功，模型 deepseek-chat 已响应（42 ms）",
  }),
  updateModelConfiguration: vi.fn(),
}));

afterEach(cleanup);

describe("SettingsButton", () => {
  it("makes theme controls discoverable from the labeled settings button", async () => {
    render(
      <ThemeProvider>
        <SettingsButton />
      </ThemeProvider>,
    );

    expect(screen.getByRole("button", { name: "打开界面主题与模型设置" })).toHaveAttribute(
      "data-tooltip",
      "主题、模型与中转站配置",
    );
    fireEvent.click(screen.getByRole("button", { name: "打开界面主题与模型设置" }));

    expect(await screen.findByText("界面主题")).toBeTruthy();
    expect(screen.getByTitle("切换为浅色主题")).toBeTruthy();
    expect(screen.getByTitle("切换为深色主题")).toBeTruthy();
    expect(screen.getByTitle("切换为跟随系统主题")).toBeTruthy();
    expect(screen.getByRole("button", { name: "测试连接" })).toHaveAttribute(
      "title",
      "只测试当前表单，不保存配置",
    );
    fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
    expect(testModelConnection).toHaveBeenCalled();
    expect(getModelConfiguration).toHaveBeenCalled();
    expect(listModelPresets).toHaveBeenCalled();
  });
});

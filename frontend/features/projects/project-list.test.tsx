import React from "react";
import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectList } from "./project-list";

vi.mock("../../lib/api", () => ({
  listProjects: vi.fn().mockResolvedValue({ items: [], total: 0 }),
  createProject: vi.fn(),
  deleteProject: vi.fn(),
}));

afterEach(cleanup);

describe("ProjectList", () => {
  it("explains an empty create request and focuses the project name", async () => {
    render(<ProjectList />);
    await waitFor(() => expect(screen.queryByText("正在加载项目")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "新建项目" }));

    expect(screen.getByRole("alert")).toHaveTextContent("请先输入项目名称");
    expect(screen.getByLabelText("项目名称")).toHaveFocus();
  });
});

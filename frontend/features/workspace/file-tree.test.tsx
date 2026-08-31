import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FileNode } from "../../types/api";
import { FileTree } from "./file-tree";

afterEach(cleanup);

const nodes: FileNode[] = [
  {
    name: "analysis",
    path: "analysis",
    kind: "directory",
    children: [
      { name: "summary.json", path: "analysis/summary.json", kind: "file" },
    ],
  },
];

describe("FileTree", () => {
  it("collapses and expands a directory from its arrow button", () => {
    render(<FileTree nodes={nodes} onSelect={vi.fn()} />);

    expect(screen.getByRole("button", { name: "summary.json" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "折叠analysis" }));
    expect(screen.queryByRole("button", { name: "summary.json" })).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "展开analysis" }));
    expect(screen.getByRole("button", { name: "summary.json" })).toBeTruthy();
  });

  it("collapses and expands a directory when its name is clicked", () => {
    render(<FileTree nodes={nodes} onSelect={vi.fn()} />);

    fireEvent.click(screen.getByText("analysis"));
    expect(screen.queryByRole("button", { name: "summary.json" })).toBeNull();

    fireEvent.click(screen.getByText("analysis"));
    expect(screen.getByRole("button", { name: "summary.json" })).toBeTruthy();
  });

  it("still selects a file after its directory is expanded", () => {
    const onSelect = vi.fn();
    render(<FileTree nodes={nodes} onSelect={onSelect} />);

    fireEvent.click(screen.getByRole("button", { name: "summary.json" }));
    expect(onSelect).toHaveBeenCalledWith(nodes[0].children?.[0]);
  });

  it("allows an empty directory to collapse and expand", () => {
    render(
      <FileTree
        nodes={[{ name: "reports", path: "reports", kind: "directory", children: [] }]}
        onSelect={vi.fn()}
      />,
    );

    const collapse = screen.getByRole("button", { name: "折叠reports" });
    fireEvent.click(collapse);
    expect(screen.getByRole("button", { name: "展开reports" })).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "展开reports" }));
    expect(screen.getByRole("button", { name: "折叠reports" })).toBeTruthy();
  });
});

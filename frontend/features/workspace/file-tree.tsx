"use client";

import React, { useState } from "react";
import { ChevronRight, File, Folder } from "lucide-react";
import type { FileNode } from "@/types/api";

export function FileTree({ nodes, selected, onSelect }: { nodes: FileNode[]; selected?: string; onSelect: (node: FileNode) => void }) {
  const [collapsedPaths, setCollapsedPaths] = useState<Set<string>>(() => new Set());

  function toggleDirectory(path: string) {
    setCollapsedPaths((current) => {
      const next = new Set(current);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  return <ul className="space-y-0.5">{nodes.map((node) => <TreeNode key={node.path} node={node} depth={0} selected={selected} collapsedPaths={collapsedPaths} onToggleDirectory={toggleDirectory} onSelect={onSelect} />)}</ul>;
}

function TreeNode({ node, depth, selected, collapsedPaths, onToggleDirectory, onSelect }: { node: FileNode; depth: number; selected?: string; collapsedPaths: Set<string>; onToggleDirectory: (path: string) => void; onSelect: (node: FileNode) => void }) {
  if (node.kind === "directory") {
    const isCollapsed = collapsedPaths.has(node.path);
    return (
      <li>
        <button
          type="button"
          onClick={() => onToggleDirectory(node.path)}
          aria-label={`${isCollapsed ? "展开" : "折叠"}${node.name}`}
          aria-expanded={!isCollapsed}
          className="file-tree__directory w-full text-left"
          style={{ paddingLeft: depth * 12 }}
        >
          <span className="file-tree__toggle grid h-6 w-6 shrink-0 place-items-center rounded hover:bg-[var(--surface-tint)]">
            <ChevronRight size={13} className={isCollapsed ? "" : "rotate-90"} />
          </span>
          <Folder size={14} className="text-[var(--accent)]" />
          <span className="truncate">{node.name}</span>
        </button>
        {!isCollapsed && node.children && <ul>{node.children.map((child) => <TreeNode key={child.path} node={child} depth={depth + 1} selected={selected} collapsedPaths={collapsedPaths} onToggleDirectory={onToggleDirectory} onSelect={onSelect} />)}</ul>}
      </li>
    );
  }
  return (
    <li><button type="button" onClick={() => onSelect(node)} title={node.path} className={`file-tree__file px-2 ${selected === node.path ? "is-selected" : ""}`} style={{ paddingLeft: depth * 12 + 18 }}><File size={13} /><span className="truncate">{node.name}</span></button></li>
  );
}

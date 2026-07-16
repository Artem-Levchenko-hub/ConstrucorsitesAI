"use client";

import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  FileCode2,
  Folder,
  FolderOpen,
  ChevronRight,
  Download,
  Copy,
  Check,
} from "lucide-react";
import { getSnapshotWithFiles } from "@/lib/api/snapshots";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatBytes } from "@/lib/parse-assistant";
import { fileIcon } from "@/lib/file-icons";

/**
 * Просмотр исходного кода снапшота: слева — ДЕРЕВО файлов (папки + типовые
 * иконки), справа — содержимое выбранного файла. Файлы тянутся одним запросом
 * `GET /api/projects/:pid/snapshots/:sid` (см. apps/api/.../snapshots.py:55).
 *
 * Подсветка синтаксиса — не делаем в v1 (Prism/Shiki = ~50KB). Простой
 * <pre> с моноширинным шрифтом читается и так. Можно добавить позже.
 */

/* ─────────────────────── tree model ───────────────────────────────────── */
type FileLeaf = { kind: "file"; name: string; path: string; size: number };
type DirNode = { kind: "dir"; name: string; path: string; children: TreeNode[] };
type TreeNode = FileLeaf | DirNode;

function sortNodes(nodes: TreeNode[]): TreeNode[] {
  return [...nodes].sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "dir" ? -1 : 1; // dirs first
    return a.name.localeCompare(b.name);
  });
}

/** Build a nested folder tree from flat `path → body`, then collapse
 *  single-child directory chains (`src › app › (app)` → one `src/app/(app)`
 *  row) so deep Next.js routes stay readable. */
function buildTree(files: Record<string, string>): TreeNode[] {
  const root: DirNode = { kind: "dir", name: "", path: "", children: [] };

  for (const path of Object.keys(files)) {
    const parts = path.split("/").filter(Boolean);
    let cur = root;
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i];
      const isLeaf = i === parts.length - 1;
      if (isLeaf) {
        cur.children.push({
          kind: "file",
          name: part,
          path,
          size: new Blob([files[path] ?? ""]).size,
        });
      } else {
        const dirPath = parts.slice(0, i + 1).join("/");
        let next = cur.children.find(
          (c): c is DirNode => c.kind === "dir" && c.path === dirPath,
        );
        if (!next) {
          next = { kind: "dir", name: part, path: dirPath, children: [] };
          cur.children.push(next);
        }
        cur = next;
      }
    }
  }

  const collapseChain = (node: DirNode): DirNode => {
    let n = node;
    // Merge a dir that holds exactly one sub-dir (and no files) into its child.
    while (n.children.length === 1 && n.children[0].kind === "dir") {
      const only = n.children[0] as DirNode;
      n = { kind: "dir", name: `${n.name}/${only.name}`, path: only.path, children: only.children };
    }
    n.children = sortNodes(
      n.children.map((c) => (c.kind === "dir" ? collapseChain(c) : c)),
    );
    return n;
  };

  return sortNodes(
    root.children.map((c) => (c.kind === "dir" ? collapseChain(c) : c)),
  );
}

export function CodeView({
  projectId,
  snapshotId,
  initialFile,
}: {
  projectId: string;
  snapshotId: string;
  initialFile?: string | null;
}) {
  const { data, isPending, isError } = useQuery({
    queryKey: ["snapshot-files", projectId, snapshotId],
    queryFn: () => getSnapshotWithFiles(projectId, snapshotId),
    staleTime: 5 * 60_000,
  });

  const paths = useMemo(
    () => (data?.files ? Object.keys(data.files).sort() : []),
    [data?.files],
  );
  const tree = useMemo(
    () => (data?.files ? buildTree(data.files) : []),
    [data?.files],
  );

  const [active, setActive] = useState<string | null>(initialFile ?? null);
  useEffect(() => {
    if (paths.length === 0) return;
    if (!active || !paths.includes(active)) setActive(paths[0]);
  }, [paths, active]);

  // Collapsed folders by path (default: everything expanded).
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const toggleDir = (p: string) =>
    setCollapsed((m) => ({ ...m, [p]: !m[p] }));

  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(t);
  }, [copied]);

  if (isPending) {
    return (
      <div className="h-full flex">
        <div className="w-60 border-r border-border-subtle p-3 space-y-2">
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-full" />
          <Skeleton className="h-5 w-2/3" />
        </div>
        <div className="flex-1 p-4">
          <Skeleton className="h-full w-full" />
        </div>
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-fg-tertiary">
        Не удалось загрузить файлы
      </div>
    );
  }
  if (paths.length === 0) {
    return (
      <div className="h-full flex items-center justify-center text-xs text-fg-tertiary">
        В этом снапшоте нет файлов.
      </div>
    );
  }

  const activeBody = active ? (data.files[active] ?? "") : "";
  const activeSize = new Blob([activeBody]).size;
  const activeName = active ? (active.split("/").pop() ?? active) : "";
  const ActiveIcon = active ? fileIcon(activeName).Icon : FileCode2;
  const activeColor = active ? fileIcon(activeName).color : "text-fg-tertiary";

  const downloadAll = () => {
    if (!active) return;
    const blob = new Blob([activeBody], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = active.split("/").pop() ?? "file.txt";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const copyAll = async () => {
    try {
      await navigator.clipboard.writeText(activeBody);
      setCopied(true);
    } catch {
      // ignore — некоторые браузеры/iframe сэндбоксы блокируют clipboard
    }
  };

  /* Recursive tree row. `depth` drives indentation; folders toggle, files
   * select. Kept inline so it closes over active/collapsed/toggle. */
  const renderNode = (node: TreeNode, depth: number): ReactNode => {
    const pad = { paddingLeft: `${depth * 12 + 10}px` };
    if (node.kind === "dir") {
      const isOpen = !collapsed[node.path];
      return (
        <div key={node.path}>
          <button
            type="button"
            onClick={() => toggleDir(node.path)}
            style={pad}
            className="w-full text-left pr-2 py-1 flex items-center gap-1.5 text-fg-secondary transition-colors hover:bg-surface-raised hover:text-fg-primary"
          >
            <ChevronRight
              className={cn(
                "h-3 w-3 shrink-0 text-fg-tertiary transition-transform",
                isOpen && "rotate-90",
              )}
            />
            {isOpen ? (
              <FolderOpen className="h-3.5 w-3.5 shrink-0 text-amber-400/80" />
            ) : (
              <Folder className="h-3.5 w-3.5 shrink-0 text-amber-400/80" />
            )}
            <span className="font-mono text-xs truncate">{node.name}</span>
          </button>
          {isOpen &&
            node.children.map((child) => renderNode(child, depth + 1))}
        </div>
      );
    }
    const { Icon, color } = fileIcon(node.name);
    const isActive = node.path === active;
    return (
      <button
        key={node.path}
        type="button"
        onClick={() => setActive(node.path)}
        style={pad}
        className={cn(
          "w-full text-left pr-2 py-1 flex items-center gap-1.5 transition-colors",
          isActive
            ? "bg-surface-overlay text-fg-primary"
            : "text-fg-secondary hover:bg-surface-raised hover:text-fg-primary",
        )}
      >
        <span className="w-3 shrink-0" />
        <Icon className={cn("h-3.5 w-3.5 shrink-0", color)} />
        <span className="font-mono text-xs truncate flex-1 min-w-0">
          {node.name}
        </span>
        <span className="text-[10px] font-mono text-fg-tertiary shrink-0">
          {formatBytes(node.size)}
        </span>
      </button>
    );
  };

  return (
    <div className="h-full flex bg-surface-base">
      <div className="w-60 shrink-0 border-r border-border-subtle flex flex-col overflow-hidden">
        <div className="h-9 px-3 flex items-center gap-1.5 border-b border-border-subtle/60">
          <Folder className="h-3.5 w-3.5 text-fg-tertiary" />
          <span className="text-xs font-mono text-fg-tertiary uppercase tracking-wider">
            Файлы
          </span>
          <span className="ml-auto text-[11px] font-mono text-fg-tertiary">
            {paths.length}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto py-1.5 scrollbar-elegant">
          {tree.map((node) => renderNode(node, 0))}
        </div>
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        <div className="h-9 px-3 flex items-center gap-2 shrink-0 border-b border-border-subtle/60">
          {active ? (
            <>
              <ActiveIcon className={cn("h-3.5 w-3.5", activeColor)} />
              <span className="font-mono text-xs text-fg-primary truncate">
                {active}
              </span>
              <span className="text-[11px] font-mono text-fg-tertiary">
                {formatBytes(activeSize)}
              </span>
              <div className="ml-auto flex items-center gap-1">
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={copyAll}
                  className="h-7 gap-1.5"
                  title="Скопировать содержимое"
                >
                  {copied ? (
                    <Check className="h-3.5 w-3.5" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                  {copied ? "Скопировано" : "Копия"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={downloadAll}
                  className="h-7 gap-1.5"
                  title="Скачать файл"
                >
                  <Download className="h-3.5 w-3.5" />
                  Скачать
                </Button>
              </div>
            </>
          ) : (
            <span className="text-xs text-fg-tertiary">
              Выберите файл слева
            </span>
          )}
        </div>
        <pre className="flex-1 overflow-auto m-0 p-3 text-[12px] leading-[1.55] font-mono text-fg-secondary bg-surface-base whitespace-pre scrollbar-elegant">
          {activeBody}
        </pre>
      </div>
    </div>
  );
}

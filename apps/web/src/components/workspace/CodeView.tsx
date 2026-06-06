"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileCode2, Folder, Download, Copy, Check } from "lucide-react";
import { getSnapshotWithFiles } from "@/lib/api/snapshots";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { formatBytes } from "@/lib/parse-assistant";

/**
 * Просмотр исходного кода снапшота: слева — дерево файлов, справа —
 * содержимое выбранного файла. Файлы тянутся одним запросом
 * `GET /api/projects/:pid/snapshots/:sid` (см. apps/api/.../snapshots.py:55).
 *
 * Подсветка синтаксиса — не делаем в v1 (Prism/Shiki = ~50KB). Простой
 * <pre> с моноширинным шрифтом читается и так. Можно добавить позже.
 */
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

  const [active, setActive] = useState<string | null>(initialFile ?? null);
  useEffect(() => {
    if (paths.length === 0) return;
    if (!active || !paths.includes(active)) setActive(paths[0]);
  }, [paths, active]);

  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(t);
  }, [copied]);

  if (isPending) {
    return (
      <div className="h-full flex">
        <div className="w-56 border-r border-border-subtle p-3 space-y-2">
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

  const downloadAll = () => {
    // Тривиальный zip-free экспорт: один файл = просто скачать; несколько —
    // склеить в плоский текст с разделителем. Полноценный zip требует jszip
    // (~100KB); для текущего MVP избыточно.
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

  return (
    <div className="h-full flex bg-surface-base">
      <div className="w-56 shrink-0 border-r border-border-subtle flex flex-col overflow-hidden">
        <div className="h-9 px-3 flex items-center gap-1.5">
          <Folder className="h-3.5 w-3.5 text-fg-tertiary" />
          <span className="text-xs font-mono text-fg-tertiary uppercase tracking-wider">
            Файлы
          </span>
          <span className="ml-auto text-[11px] font-mono text-fg-tertiary">
            {paths.length}
          </span>
        </div>
        <div className="flex-1 overflow-y-auto py-1.5">
          {paths.map((path) => {
            const body = data.files[path] ?? "";
            const size = new Blob([body]).size;
            const isActive = path === active;
            return (
              <button
                key={path}
                type="button"
                onClick={() => setActive(path)}
                className={cn(
                  "w-full text-left px-3 py-1.5 flex items-center gap-2 transition-colors",
                  isActive
                    ? "bg-surface-overlay text-fg-primary"
                    : "text-fg-secondary hover:bg-surface-raised hover:text-fg-primary",
                )}
              >
                <FileCode2 className="h-3.5 w-3.5 shrink-0 text-fg-tertiary" />
                <span className="font-mono text-xs truncate flex-1 min-w-0">
                  {path}
                </span>
                <span className="text-[10px] font-mono text-fg-tertiary shrink-0">
                  {formatBytes(size)}
                </span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="flex-1 flex flex-col min-w-0">
        <div className="h-9 px-3 flex items-center gap-2 shrink-0">
          {active ? (
            <>
              <FileCode2 className="h-3.5 w-3.5 text-fg-tertiary" />
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
        <pre className="flex-1 overflow-auto m-0 p-3 text-[12px] leading-[1.55] font-mono text-fg-secondary bg-surface-base whitespace-pre">
          {activeBody}
        </pre>
      </div>
    </div>
  );
}

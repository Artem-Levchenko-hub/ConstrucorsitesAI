"use client";

import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ExternalLink, Loader2, Play, RefreshCw } from "lucide-react";

import { getRuntime, startRuntime } from "@/lib/api/runtime";
import type { Project } from "@/lib/api/types";

export function MaxLivePreview({ project }: { project: Project }) {
  const queryClient = useQueryClient();
  const started = useRef(false);
  const runtime = useQuery({
    queryKey: ["runtime", project.id],
    queryFn: () => getRuntime(project.id),
    retry: false,
    refetchInterval: (query) => {
      const state = query.state.data?.state;
      return state === "running" || state === "failed" ? false : 2_000;
    },
  });
  const start = useMutation({
    mutationFn: () => startRuntime(project.id),
    onSuccess: (value) =>
      queryClient.setQueryData(["runtime", project.id], value),
  });

  useEffect(() => {
    if (runtime.isLoading || started.current) return;
    if (
      runtime.isError ||
      !runtime.data ||
      ["stopped", "paused", "failed"].includes(runtime.data.state)
    ) {
      started.current = true;
      start.mutate();
    }
  }, [runtime.isLoading, runtime.isError, runtime.data, start]);

  const apiOrigin =
    process.env.NEXT_PUBLIC_API_URL ??
    (typeof window !== "undefined" ? window.location.origin : "");
  const publicUrl = `${apiOrigin.replace(/\/$/, "")}/p/${project.slug}`;
  const previewUrl =
    runtime.data?.state === "running" && runtime.data.dev_url
      ? runtime.data.dev_url
      : null;
  const connected = Boolean(previewUrl);

  return (
    <aside className="flex h-full min-h-0 flex-col bg-[#13172a] px-6 py-5">
      <div className="flex shrink-0 items-center justify-between gap-3">
        <h2 className="text-[14px] font-semibold text-white">Живое превью</h2>
        <span className="inline-flex items-center gap-2 text-[11px] text-[#7b89a4]">
          <span
            className={`h-2 w-2 rounded-full ${
              connected ? "bg-[#20c997]" : "bg-[#64748b]"
            }`}
          />
          {connected ? "Подключено" : "Запускается"}
        </span>
      </div>

      <div className="mt-5 flex min-h-0 flex-1 flex-col items-center">
        <div className="relative h-[580px] w-[290px] max-w-full shrink-0 overflow-hidden rounded-[34px] border-[8px] border-[#202538] bg-[#080a10] shadow-[0_24px_80px_rgba(0,0,0,0.28)]">
          <div className="absolute inset-x-0 top-0 z-10 flex h-7 items-center justify-between bg-[#0d1120] px-3 text-[10px] font-semibold text-white">
            <span>09:41</span>
            <span>▮▮ ◉</span>
          </div>
          {previewUrl ? (
            <iframe
              key={previewUrl}
              src={previewUrl}
              title={`Превью ${project.name}`}
              className="h-full w-full border-0 bg-white pt-7"
              allow="clipboard-read; clipboard-write"
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center px-8 text-center">
              {start.isPending || runtime.isLoading ? (
                <Loader2 className="h-6 w-6 animate-spin text-[#3b82f6]" />
              ) : (
                <Play className="h-6 w-6 text-[#3b82f6]" />
              )}
              <p className="mt-4 text-[13px] font-medium text-white">
                Подготавливаем рабочую версию
              </p>
              <p className="mt-2 text-[11px] leading-5 text-[#7b89a4]">
                Превью появится здесь после запуска контейнера.
              </p>
              {!start.isPending && (
                <button
                  type="button"
                  onClick={() => start.mutate()}
                  className="mt-5 inline-flex items-center gap-2 rounded-lg border border-[#26304f] px-3 py-2 text-[11px] text-white"
                >
                  <RefreshCw className="h-3.5 w-3.5" />
                  Повторить
                </button>
              )}
            </div>
          )}
        </div>
        <p className="mt-6 max-w-[290px] text-center text-[11px] leading-4 text-[#60708d]">
          Превью обновляется по мере того, как агент применяет изменения.
        </p>
        <a
          href={previewUrl ?? publicUrl}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-flex items-center gap-1.5 text-[11px] font-medium text-[#7ba7ff] hover:text-white"
        >
          Открыть отдельно
          <ExternalLink className="h-3 w-3" />
        </a>
      </div>
    </aside>
  );
}

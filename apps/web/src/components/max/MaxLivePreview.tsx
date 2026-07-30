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
    onSuccess: (value) => queryClient.setQueryData(["runtime", project.id], value),
  });

  useEffect(() => {
    if (runtime.isLoading || started.current) return;
    if (runtime.isError || !runtime.data || ["stopped", "paused", "failed"].includes(runtime.data.state)) {
      started.current = true;
      start.mutate();
    }
  }, [runtime.isLoading, runtime.isError, runtime.data, start]);

  // A relative same-origin fallback is both correct behind the production
  // reverse proxy and stable across SSR/hydration. Reading window.location
  // during render produced different href values on server and client.
  const apiOrigin = process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "");
  const publicUrl = apiOrigin ? `${apiOrigin}/p/${project.slug}` : `/p/${project.slug}`;
  const previewUrl = runtime.data?.state === "running" && runtime.data.dev_url ? runtime.data.dev_url : null;
  const connected = Boolean(previewUrl);

  return (
    <aside className="flex h-full min-h-0 flex-col bg-[#f5f3ee] px-5 py-4">
      <div className="flex shrink-0 items-center justify-between gap-3">
        <div>
          <p className="omnia-kicker text-[#8d887f]">Mobile WebView</p>
          <h2 className="mt-1 text-sm font-semibold">Живое превью</h2>
        </div>
        <span className="inline-flex items-center gap-2 text-[10px] text-[#6d6962]">
          <span className={`size-1.5 rounded-full ${connected ? "bg-[#248a4b]" : "bg-[#aaa59b]"}`} />
          {connected ? "Подключено" : "Запускается"}
        </span>
      </div>

      <div className="mt-4 flex min-h-0 flex-1 flex-col items-center">
        <div className="relative h-[580px] w-[290px] max-w-full shrink-0 overflow-hidden rounded-[34px] border-[7px] border-[#171716] bg-[#171716] shadow-[0_24px_70px_rgba(23,23,22,.18)]">
          <div className="absolute inset-x-0 top-0 z-10 flex h-7 items-center justify-between bg-[#171716] px-3 text-[9px] font-semibold text-white">
            <span>09:41</span><span>▮▮ ◉</span>
          </div>
          {previewUrl ? (
            <iframe key={previewUrl} src={previewUrl} title={`Превью ${project.name}`} className="h-full w-full border-0 bg-white pt-7" allow="clipboard-read; clipboard-write" />
          ) : (
            <div className="flex h-full flex-col items-center justify-center bg-[#fcfbf7] px-8 text-center">
              {start.isPending || runtime.isLoading ? <Loader2 className="size-6 animate-spin text-[#f15a38]" /> : <Play className="size-6 text-[#f15a38]" />}
              <p className="mt-4 text-[13px] font-medium text-[#171716]">Подготавливаем рабочую версию</p>
              <p className="mt-2 text-[11px] leading-5 text-[#8d887f]">Превью появится после запуска контейнера. Повторная генерация не запускается.</p>
              {!start.isPending && <button type="button" onClick={() => start.mutate()} className="mt-5 inline-flex items-center gap-2 rounded-[8px] border border-[#d8d4cb] px-3 py-2 text-[11px] text-[#171716]"><RefreshCw className="size-3.5" />Повторить запуск</button>}
            </div>
          )}
        </div>
        <p className="mt-4 max-w-[290px] text-center text-[10px] leading-4 text-[#8d887f]">Превью обновляется после сохранённых изменений агента.</p>
        <a href={previewUrl ?? publicUrl} target="_blank" rel="noreferrer" className="mt-2 inline-flex items-center gap-1.5 text-[11px] font-medium text-[#c84528] hover:underline">
          Открыть отдельно <ExternalLink className="size-3" />
        </a>
      </div>
    </aside>
  );
}

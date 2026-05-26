"use client";

/**
 * 📜 Container logs — Dialog that tails the project's dev container's
 * stdout+stderr.
 *
 * Polls `/api/projects/<id>/runtime/logs?tail=200` every 3 s while open.
 * No follow-stream WebSocket — Next.js dev logs are bursty (compile +
 * warning flurry, then quiet), and react-query's stale-time gives us
 * natural throttling that a raw `tail -f` doesn't.
 *
 * Hidden when the project isn't fullstack (V1 static projects render via
 * Playwright PNG snapshots, no container, no logs to read).
 */

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ScrollText } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { getRuntimeLogs } from "@/lib/api/runtime";
import { cn } from "@/lib/utils";

export function LogsViewer({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);
  const [tail, setTail] = useState(200);
  const scrollRef = useRef<HTMLPreElement>(null);

  const { data, isFetching, isError, refetch } = useQuery({
    queryKey: ["runtime-logs", projectId, tail],
    queryFn: () => getRuntimeLogs(projectId, tail, "dev"),
    enabled: open,
    refetchInterval: open ? 3_000 : false,
    refetchOnWindowFocus: false,
    staleTime: 0,
  });

  // Stick to the bottom whenever new logs land. We DON'T auto-scroll if the
  // user manually scrolled up to read older lines — detect via threshold.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !data?.logs) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  }, [data?.logs]);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button
          size="sm"
          variant="ghost"
          className="gap-1.5 px-2.5 h-7"
          title="Логи dev-контейнера"
        >
          <ScrollText className="h-3.5 w-3.5" />
          <span className="text-xs">Логи</span>
        </Button>
      </DialogTrigger>

      <DialogContent className="max-w-4xl max-h-[80vh] flex flex-col gap-2 p-0">
        <DialogHeader className="px-5 pt-4 pb-2 border-b border-border-subtle">
          <DialogTitle className="text-sm font-medium flex items-center gap-2">
            <ScrollText className="h-4 w-4 text-fg-tertiary" />
            Логи dev-контейнера
            {isFetching && (
              <span className="text-[10px] uppercase tracking-wider text-fg-tertiary">
                · обновление…
              </span>
            )}
          </DialogTitle>
          <div className="flex items-center gap-3 mt-2 text-xs text-fg-tertiary">
            <span>
              Контейнер:{" "}
              <span className="font-mono">
                {data?.container_name ?? "—"}
              </span>
            </span>
            <div className="ml-auto flex items-center gap-1">
              <span>Строк:</span>
              {[200, 500, 1000].map((n) => (
                <button
                  key={n}
                  type="button"
                  onClick={() => setTail(n)}
                  className={cn(
                    "px-1.5 py-0.5 rounded transition-colors",
                    tail === n
                      ? "text-accent bg-accent-subtle"
                      : "hover:text-fg-secondary",
                  )}
                >
                  {n}
                </button>
              ))}
              <button
                type="button"
                onClick={() => refetch()}
                className="ml-2 px-2 py-0.5 rounded border border-border-default hover:border-border-strong"
              >
                Обновить
              </button>
            </div>
          </div>
        </DialogHeader>

        <pre
          ref={scrollRef}
          className="flex-1 min-h-[400px] max-h-[60vh] overflow-auto px-5 py-3 m-0 text-[11px] leading-relaxed font-mono whitespace-pre-wrap break-words bg-surface-base text-fg-secondary"
        >
          {isError ? (
            <span className="text-red-400">
              Не удалось загрузить логи. Попробуйте «Обновить».
            </span>
          ) : !data ? (
            <span className="text-fg-tertiary">Загрузка…</span>
          ) : data.logs ? (
            data.logs
          ) : (
            <span className="text-fg-tertiary">
              Логов пока нет. Контейнер только что стартовал или ещё не
              провижнен.
            </span>
          )}
        </pre>
      </DialogContent>
    </Dialog>
  );
}

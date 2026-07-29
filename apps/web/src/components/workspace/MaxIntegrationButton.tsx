"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  CheckCircle2,
  Copy,
  ExternalLink,
  Loader2,
  Radio,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api/client";
import {
  activateMaxIntegration,
  connectMaxIntegration,
  disconnectMaxIntegration,
  getMaxIntegration,
  verifyMaxIntegration,
} from "@/lib/api/max-integration";
import { getProject } from "@/lib/api/projects";

function message(error: unknown): string {
  return error instanceof ApiError ? error.message : "Не удалось выполнить действие";
}

export function MaxIntegrationButton({
  projectId,
  initialTemplate,
}: {
  projectId: string;
  initialTemplate?: string;
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const queryKey = ["max-integration", projectId];
  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => getProject(projectId),
  });
  const isMax =
    initialTemplate === "max_miniapp" || project.data?.template === "max_miniapp";
  const integration = useQuery({
    queryKey,
    queryFn: () => getMaxIntegration(projectId),
    enabled: open,
  });

  const connect = useMutation({
    mutationFn: () => connectMaxIntegration(projectId, token),
    onSuccess: (data) => {
      qc.setQueryData(queryKey, data);
      setToken("");
      toast.success("MAX-бот подключён");
    },
    onError: (error) =>
      toast.error("Не удалось подключить MAX", { description: message(error) }),
  });
  const verify = useMutation({
    mutationFn: () => verifyMaxIntegration(projectId),
    onSuccess: (data) => {
      qc.setQueryData(queryKey, data);
      toast.success("Подключение MAX работает");
    },
    onError: (error) =>
      toast.error("Проверка не прошла", { description: message(error) }),
  });
  const activate = useMutation({
    mutationFn: () => activateMaxIntegration(projectId),
    onSuccess: (data) => {
      qc.setQueryData(queryKey, data);
      toast.success("Webhook MAX активирован");
    },
    onError: (error) =>
      toast.error("Не удалось активировать", { description: message(error) }),
  });
  const disconnect = useMutation({
    mutationFn: () => disconnectMaxIntegration(projectId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey });
      toast.success("Интеграция MAX отключена");
    },
    onError: (error) =>
      toast.error("Не удалось отключить", { description: message(error) }),
  });

  const data = integration.data;
  const busy =
    connect.isPending ||
    verify.isPending ||
    activate.isPending ||
    disconnect.isPending;

  async function copy(value: string, label: string) {
    await navigator.clipboard.writeText(value);
    toast.success(`${label} скопирован`);
  }

  if (!isMax) return null;

  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => setOpen(true)}
        className="h-7 gap-1.5 px-2.5 text-xs"
        title="Подключить MAX Mini App"
        data-testid="max-integration-open"
      >
        <Bot className="h-3.5 w-3.5" />
        <span className="hidden 2xl:inline">MAX</span>
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[88vh] overflow-y-auto sm:max-w-[540px]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Bot className="h-5 w-5 text-accent" />
              MAX Mini App
            </DialogTitle>
            <DialogDescription>
              Подключите прошедшего модерацию MAX-бота, опубликуйте проект и
              активируйте защищённый webhook.
            </DialogDescription>
          </DialogHeader>

          {integration.isLoading ? (
            <div className="flex min-h-32 items-center justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-fg-tertiary" />
            </div>
          ) : integration.isError ? (
            <div className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-sm">
              {message(integration.error)}
            </div>
          ) : !data?.connected ? (
            <div className="space-y-4" data-testid="max-connect-form">
              <div className="rounded-lg border border-border-subtle bg-surface-raised p-3 text-xs leading-relaxed text-fg-secondary">
                Бот создаётся и проходит модерацию на платформе MAX для
                партнёров. Секрет сохраняется зашифрованно и не показывается
                повторно.
              </div>
              <div className="space-y-2">
                <Label htmlFor="max-bot-token">Секрет бота</Label>
                <Input
                  id="max-bot-token"
                  type="password"
                  autoComplete="off"
                  value={token}
                  onChange={(event) => setToken(event.target.value)}
                  placeholder="Вставьте значение из кабинета MAX"
                  data-testid="max-token-input"
                />
              </div>
              <Button
                className="w-full"
                disabled={token.trim().length < 10 || connect.isPending}
                onClick={() => connect.mutate()}
                data-testid="max-connect-submit"
              >
                {connect.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Проверить и подключить
              </Button>
              <a
                href="https://business.max.ru/"
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-center gap-1 text-xs text-accent hover:underline"
              >
                Открыть платформу MAX для партнёров
                <ExternalLink className="h-3 w-3" />
              </a>
            </div>
          ) : (
            <div className="space-y-4" data-testid="max-connected-panel">
              <div className="flex items-start justify-between gap-3 rounded-xl border border-border-subtle bg-surface-raised p-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="truncate text-sm font-semibold">
                      {data.bot_name || data.bot_username || "MAX-бот"}
                    </p>
                    <Badge variant={data.status === "active" ? "success" : "default"}>
                      {data.status === "active"
                        ? "Активно"
                        : data.status === "error"
                          ? "Ошибка"
                          : "Подключено"}
                    </Badge>
                  </div>
                  <p className="mt-1 text-xs text-fg-tertiary">
                    {data.bot_username ? `@${data.bot_username}` : `ID ${data.bot_id || "—"}`}
                  </p>
                </div>
                <CheckCircle2 className="h-5 w-5 shrink-0 text-success" />
              </div>

              {data.last_error && (
                <p className="rounded-lg border border-danger/30 bg-danger/10 p-3 text-xs text-danger">
                  {data.last_error}
                </p>
              )}

              <div className="grid grid-cols-2 gap-2">
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() => verify.mutate()}
                >
                  <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                  Проверить
                </Button>
                <Button
                  disabled={busy}
                  onClick={() => activate.mutate()}
                  data-testid="max-activate"
                >
                  {activate.isPending ? (
                    <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Radio className="mr-1.5 h-3.5 w-3.5" />
                  )}
                  Активировать
                </Button>
              </div>

              {data.app_url ? (
                <div className="space-y-3 rounded-xl border border-border-subtle p-4">
                  <div>
                    <p className="text-xs font-medium">URL мини-приложения</p>
                    <div className="mt-1 flex items-center gap-2">
                      <code className="min-w-0 flex-1 truncate text-xs text-fg-secondary">
                        {data.app_url}
                      </code>
                      <Button
                        size="icon"
                        variant="ghost"
                        onClick={() => copy(data.app_url!, "URL")}
                        aria-label="Скопировать URL"
                      >
                        <Copy className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                  <p className="text-xs leading-relaxed text-fg-secondary">
                    Вставьте этот URL в MAX для партнёров → Чат-боты →
                    Расширенные настройки → Мини-приложение. MAX не предоставляет
                    публичного API для этого шага.
                  </p>
                  {data.deep_link && (
                    <a
                      href={`${data.deep_link}?startapp`}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-xs text-accent hover:underline"
                    >
                      Открыть ссылку запуска
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>
              ) : (
                <p className="rounded-lg border border-border-subtle p-3 text-xs leading-relaxed text-fg-secondary">
                  Сначала нажмите «Опубликовать», дождитесь стабильного HTTPS,
                  затем вернитесь и нажмите «Активировать».
                </p>
              )}

              <details className="rounded-lg border border-border-subtle">
                <summary className="cursor-pointer px-3 py-2 text-xs text-fg-secondary">
                  Заменить секрет бота
                </summary>
                <div className="space-y-2 border-t border-border-subtle p-3">
                  <Input
                    type="password"
                    autoComplete="off"
                    value={token}
                    onChange={(event) => setToken(event.target.value)}
                    placeholder="Новое значение"
                  />
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={token.trim().length < 10 || connect.isPending}
                    onClick={() => connect.mutate()}
                  >
                    Заменить
                  </Button>
                </div>
              </details>

              <Button
                variant="ghost"
                className="w-full text-danger hover:text-danger"
                disabled={busy}
                onClick={() => {
                  if (window.confirm("Отключить webhook и удалить интеграцию MAX?")) {
                    disconnect.mutate();
                  }
                }}
              >
                <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                Отключить MAX
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

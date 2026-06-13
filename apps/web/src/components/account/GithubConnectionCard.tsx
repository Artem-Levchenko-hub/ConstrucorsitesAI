"use client";

/**
 * Карточка «GitHub» на странице /account.
 *
 *   - useQuery(["github-status"]) — показывает «Подключён как @login» или «Не подключён».
 *   - Подключение: GET /api/github/connect → window.location.assign(authorize_url) →
 *     GitHub OAuth → callback на бэкенде → редирект сюда с ?github=connected|denied|error.
 *   - useSearchParams листенер ловит результат: toast + invalidate + чистка query.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, Github, Loader2, Unplug } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  disconnectGithub,
  getGithubConnectUrl,
  getGithubStatus,
} from "@/lib/api/github";

/**
 * Публичный API карточки. Оборачивает внутренний компонент в Suspense, потому что
 * он читает `useSearchParams` — без границы Next 15 валит prerender всей страницы
 * (`missing-suspense-with-csr-bailout`). Граница живёт ЗДЕСЬ, у источника, чтобы
 * любой потребитель `<GithubConnectionCard />` получал защиту автоматически.
 */
export function GithubConnectionCard() {
  return (
    <Suspense fallback={<GithubConnectionCardFallback />}>
      <GithubConnectionCardInner />
    </Suspense>
  );
}

/** Каркас на время гидрации границы — повторяет рамку карточки, без серого бокса. */
function GithubConnectionCardFallback() {
  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle className="flex items-center gap-2 text-base">
          <Github className="h-4 w-4" />
          GitHub
        </CardTitle>
        <CardDescription>
          Заливай проекты Omnia.AI в свои репозитории одним кликом.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex items-center gap-2 text-sm text-fg-tertiary">
          <Loader2 className="h-4 w-4 animate-spin" />
          Проверяем подключение…
        </div>
      </CardContent>
    </Card>
  );
}

function GithubConnectionCardInner() {
  const qc = useQueryClient();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const { data, isPending } = useQuery({
    queryKey: ["github-status"],
    queryFn: getGithubStatus,
    retry: false,
    staleTime: 60_000,
  });

  const [connecting, setConnecting] = useState(false);
  const toasted = useRef(false);

  // OAuth callback feedback — backend редиректит сюда с ?github=...
  useEffect(() => {
    if (toasted.current) return;
    const outcome = searchParams.get("github");
    if (!outcome) return;
    toasted.current = true;
    if (outcome === "connected") {
      toast.success("GitHub подключён");
    } else if (outcome === "denied") {
      toast.error("Доступ к GitHub отклонён");
    } else if (outcome === "error") {
      toast.error("Не удалось подключить GitHub");
    }
    qc.invalidateQueries({ queryKey: ["github-status"] });
    router.replace(pathname);
  }, [searchParams, qc, router, pathname]);

  const disconnectMut = useMutation({
    mutationFn: disconnectGithub,
    onSuccess: () => {
      toast.success("GitHub отключён");
      qc.invalidateQueries({ queryKey: ["github-status"] });
    },
    onError: (err: unknown) => {
      const msg = err instanceof Error ? err.message : "ошибка";
      toast.error("Не удалось отключить", { description: msg });
    },
  });

  const handleConnect = async () => {
    setConnecting(true);
    try {
      const { authorize_url } = await getGithubConnectUrl();
      window.location.assign(authorize_url);
    } catch (err) {
      setConnecting(false);
      const msg = err instanceof Error ? err.message : "ошибка";
      toast.error("Не удалось начать подключение", { description: msg });
    }
  };

  return (
    <Card>
      <CardHeader className="space-y-1">
        <CardTitle className="flex items-center gap-2 text-base">
          <Github className="h-4 w-4" />
          GitHub
        </CardTitle>
        <CardDescription>
          Заливай проекты Omnia.AI в свои репозитории одним кликом.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        {isPending ? (
          <div className="flex items-center gap-2 text-sm text-fg-tertiary">
            <Loader2 className="h-4 w-4 animate-spin" />
            Проверяем подключение…
          </div>
        ) : data?.connected ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-sm text-fg-primary">
              <CircleCheck className="h-4 w-4 text-success" />
              Подключён как{" "}
              <span className="font-mono text-fg-secondary">
                @{data.login}
              </span>
            </div>
            <Button
              variant="secondary"
              onClick={() => disconnectMut.mutate()}
              disabled={disconnectMut.isPending}
              className="gap-2"
            >
              {disconnectMut.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Unplug className="h-4 w-4" />
              )}
              Отключить
            </Button>
          </div>
        ) : (
          <div className="space-y-3">
            <p className="text-sm text-fg-tertiary">
              GitHub не подключён.
            </p>
            <Button
              onClick={handleConnect}
              disabled={connecting}
              className="gap-2"
            >
              {connecting ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Github className="h-4 w-4" />
              )}
              Подключить GitHub
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

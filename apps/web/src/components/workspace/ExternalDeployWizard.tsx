"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  ExternalLink,
  Loader2,
  Rocket,
  Server,
  ShieldCheck,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  createDeployTarget,
  verifyDeployTarget,
  type DeployTarget,
  type DeployTargetVerifyResult,
} from "@/lib/api/deploy-targets";
import {
  ExternalDeployDnsError,
  launchExternalDeploy,
} from "@/lib/api/external-deploy";
import { getLastDeploy } from "@/lib/api/runtime";

function message(error: unknown): string {
  return error instanceof Error ? error.message : "Что-то пошло не так";
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <Label className="mb-1 block text-xs text-fg-secondary">{label}</Label>
      {children}
      {hint && <p className="mt-1 text-[10px] text-fg-tertiary">{hint}</p>}
    </div>
  );
}

export function ExternalDeployWizard({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient();
  const [domain, setDomain] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [user, setUser] = useState("root");
  const [authType, setAuthType] = useState<"password" | "key">("password");
  const [secret, setSecret] = useState("");
  const [target, setTarget] = useState<DeployTarget | null>(null);
  const [verification, setVerification] =
    useState<DeployTargetVerifyResult | null>(null);
  const [deployStarted, setDeployStarted] = useState(false);
  const [dnsWaiting, setDnsWaiting] = useState(false);

  const deployQuery = useQuery({
    queryKey: ["deploy", projectId],
    queryFn: () => getLastDeploy(projectId),
    enabled: deployStarted,
    refetchInterval: (query) =>
      ["building", "pushing", "swapping", "cancelling"].includes(
        query.state.data?.phase ?? "",
      )
        ? 1_500
        : false,
    retry: false,
  });

  const discoverMutation = useMutation({
    mutationFn: async () => {
      const created = await createDeployTarget({
        label: domain.trim() || `VPS ${host.trim()}`,
        ssh_host: host.trim(),
        ssh_port: Number(port) || 22,
        ssh_user: user.trim() || "root",
        auth_type: authType,
        secret: secret.trim(),
      });
      const discovered = await verifyDeployTarget(created.id, false);
      return { created, discovered };
    },
    onSuccess: ({ created, discovered }) => {
      setTarget(created);
      setVerification(discovered);
      setDnsWaiting(false);
      queryClient.invalidateQueries({ queryKey: ["deploy-targets"] });
      if (discovered.requires_confirmation) {
        toast.message("Сверьте SSH-отпечаток", {
          description: "Доступ будет использован только после подтверждения.",
        });
      } else if (!discovered.ok) {
        toast.error("Сервер не прошёл проверку", {
          description: discovered.detail ?? undefined,
        });
      }
    },
    onError: (error) =>
      toast.error("Не удалось проверить VPS", { description: message(error) }),
  });

  const launchMutation = useMutation({
    mutationFn: async () => {
      if (!target) throw new Error("Сначала проверьте VPS.");
      const confirmed = await verifyDeployTarget(target.id, true);
      if (!confirmed.ok) {
        throw new Error(confirmed.detail ?? "VPS не прошёл защищённую проверку.");
      }
      const verifiedTarget: DeployTarget = {
        ...target,
        verify_status: "ok",
        verify_detail: confirmed.detail,
        host_fingerprint:
          confirmed.host_fingerprint ?? target.host_fingerprint,
        resolved_ip: confirmed.resolved_ip ?? target.resolved_ip,
        capabilities: confirmed.capabilities,
      };
      setTarget(verifiedTarget);
      return launchExternalDeploy({
        projectId,
        target: verifiedTarget,
        domainHost: domain.trim() || undefined,
      });
    },
    onSuccess: ({ deploy }) => {
      setDnsWaiting(false);
      setDeployStarted(true);
      queryClient.setQueryData(["deploy", projectId], deploy);
      queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      queryClient.invalidateQueries({ queryKey: ["deploy-targets"] });
      queryClient.invalidateQueries({ queryKey: ["domains", projectId] });
      toast.success("Развёртывание запущено", {
        description: domain.trim()
          ? "После health-check сайт откроется по вашему домену."
          : "После health-check сайт откроется по IP и выделенному порту.",
      });
    },
    onError: (error) => {
      const waiting = error instanceof ExternalDeployDnsError;
      setDnsWaiting(waiting);
      toast.error(waiting ? "A-запись ещё не готова" : "Запуск не удался", {
        description: message(error),
      });
    },
  });

  const reset = () => {
    setTarget(null);
    setVerification(null);
    setDeployStarted(false);
    setDnsWaiting(false);
  };

  const activeDeploy = ["building", "pushing", "swapping", "cancelling"].includes(
    deployQuery.data?.phase ?? "",
  );
  const completed = deployQuery.data?.phase === "done";
  const failed = deployQuery.data?.phase === "failed";
  const canDiscover =
    host.trim().length > 0 &&
    user.trim().length > 0 &&
    secret.trim().length > 0 &&
    !target;

  return (
    <section
      className="space-y-4 rounded-xl border border-[#d8d4cb] bg-[#fcfbf7] p-4"
      data-testid="external-deploy-wizard"
    >
      <div className="flex items-start gap-3">
        <span className="rounded-lg bg-[#f15a38]/10 p-2 text-[#f15a38]">
          <Rocket className="h-4 w-4" />
        </span>
        <div>
          <h3 className="text-sm font-semibold">Развернуть на своей VPS</h3>
          <p className="mt-1 text-[11px] leading-relaxed text-fg-secondary">
            Введите IP и SSH-доступ. Если домен уже направлен A-записью на этот
            IP, добавьте его — DNS, HTTPS, сборку и запуск мы проверим сами.
          </p>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 text-[10px] text-fg-tertiary">
        {[
          "Вы указали A-запись у регистратора",
          "Мы проверяем VPS и DNS",
          "Проект запускается по HTTPS",
        ].map((step, index) => (
          <div key={step} className="rounded-md bg-surface-overlay/70 p-2">
            <span className="mb-1 block font-semibold text-[#f15a38]">
              0{index + 1}
            </span>
            {step}
          </div>
        ))}
      </div>

      {!deployStarted && (
        <>
          <div className="grid grid-cols-2 gap-2">
            <Field
              label="Домен"
              hint="Необязательно: без домена сайт откроется по IP и порту."
            >
              <Input
                value={domain}
                onChange={(event) => {
                  setDomain(event.target.value);
                  reset();
                }}
                placeholder="site.example.ru"
                className="h-11 sm:h-8"
                disabled={!!target}
                data-testid="external-deploy-domain"
              />
            </Field>
            <Field label="Публичный IP VPS">
              <Input
                value={host}
                onChange={(event) => {
                  setHost(event.target.value);
                  reset();
                }}
                placeholder="203.0.113.10"
                className="h-11 sm:h-8"
                disabled={!!target}
                data-testid="external-deploy-host"
              />
            </Field>
            <Field label="SSH-пользователь">
              <Input
                value={user}
                onChange={(event) => {
                  setUser(event.target.value);
                  reset();
                }}
                placeholder="root"
                className="h-11 sm:h-8"
                disabled={!!target}
              />
            </Field>
            <Field label="SSH-порт">
              <Input
                value={port}
                onChange={(event) => {
                  setPort(event.target.value);
                  reset();
                }}
                placeholder="22"
                inputMode="numeric"
                className="h-11 sm:h-8"
                disabled={!!target}
              />
            </Field>
          </div>

          <div>
            <Label className="mb-1 block text-xs text-fg-secondary">
              Способ входа
            </Label>
            <div className="flex gap-1.5">
              <button
                type="button"
                onClick={() => {
                  setAuthType("password");
                  reset();
                }}
                disabled={!!target}
                className={`flex-1 rounded-md border px-2 py-1.5 text-xs ${
                  authType === "password"
                    ? "border-[#f15a38] bg-[#f15a38]/10"
                    : "border-border-subtle text-fg-secondary"
                }`}
              >
                Логин + пароль
              </button>
              <button
                type="button"
                onClick={() => {
                  setAuthType("key");
                  reset();
                }}
                disabled={!!target}
                className={`flex-1 rounded-md border px-2 py-1.5 text-xs ${
                  authType === "key"
                    ? "border-[#f15a38] bg-[#f15a38]/10"
                    : "border-border-subtle text-fg-secondary"
                }`}
              >
                Приватный SSH-ключ
              </button>
            </div>
          </div>

          <Field
            label={authType === "password" ? "Пароль SSH" : "Приватный ключ OpenSSH"}
          >
            {authType === "password" ? (
              <Input
                type="password"
                value={secret}
                onChange={(event) => {
                  setSecret(event.target.value);
                  reset();
                }}
                placeholder="Пароль от VPS"
                className="h-11 sm:h-8"
                disabled={!!target}
                data-testid="external-deploy-secret"
              />
            ) : (
              <textarea
                value={secret}
                onChange={(event) => {
                  setSecret(event.target.value);
                  reset();
                }}
                placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
                className="h-20 w-full resize-y rounded-md border border-border-subtle bg-surface-overlay px-2 py-1.5 font-mono text-xs"
                disabled={!!target}
              />
            )}
          </Field>

          {!target && (
            <Button
              size="sm"
              className="h-11 w-full gap-2 text-xs sm:h-8"
              disabled={!canDiscover || discoverMutation.isPending}
              onClick={() => discoverMutation.mutate()}
              data-testid="external-deploy-check"
            >
              {discoverMutation.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Server className="h-3.5 w-3.5" />
              )}
              Проверить IP и SSH
            </Button>
          )}

          {target && verification?.requires_confirmation && (
            <div className="space-y-3 rounded-lg border border-warning/35 bg-warning/[0.07] p-3">
              <div className="flex items-start gap-2">
                <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                <div>
                  <p className="text-xs font-semibold">
                    Подтвердите, что это ваш сервер
                  </p>
                  <p className="mt-1 break-all font-mono text-[11px] text-fg-secondary">
                    {verification.host_fingerprint}
                  </p>
                  <p className="mt-1 text-[10px] text-fg-tertiary">
                    IP: {verification.resolved_ip}. До подтверждения пароль или
                    ключ не отправляется на VPS.
                  </p>
                </div>
              </div>
              <div className="flex gap-2">
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-11 text-xs sm:h-7"
                  onClick={reset}
                >
                  Изменить данные
                </Button>
                <Button
                  size="sm"
                  className="h-11 flex-1 gap-1.5 text-xs sm:h-7"
                  disabled={launchMutation.isPending}
                  onClick={() => launchMutation.mutate()}
                  data-testid="external-deploy-confirm"
                >
                  {launchMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Rocket className="h-3.5 w-3.5" />
                  )}
                  {dnsWaiting
                    ? "Проверить DNS и запустить"
                    : "Отпечаток совпадает — развернуть"}
                </Button>
              </div>
              {dnsWaiting && (
                <p className="text-[11px] text-warning">
                  VPS уже выбран. Исправьте или дождитесь A-записи у регистратора,
                  затем нажмите кнопку ещё раз — дублировать сервер не нужно.
                </p>
              )}
            </div>
          )}

          {target &&
            verification &&
            !verification.requires_confirmation &&
            !verification.ok && (
              <div className="rounded-md border border-danger/30 bg-danger/[0.06] p-3">
                <p className="text-xs text-danger">
                  {verification.detail ?? "Сервер не прошёл проверку."}
                </p>
                <Button
                  size="sm"
                  variant="ghost"
                  className="mt-2 h-11 text-xs sm:h-7"
                  onClick={reset}
                >
                  Изменить данные
                </Button>
              </div>
            )}
        </>
      )}

      {deployStarted && (
        <div className="space-y-3 rounded-lg border border-border-subtle bg-surface-overlay/70 p-3">
          <div className="flex items-center gap-2">
            {completed ? (
              <CheckCircle2 className="h-4 w-4 text-success" />
            ) : activeDeploy || deployQuery.isFetching ? (
              <Loader2 className="h-4 w-4 animate-spin text-[#f15a38]" />
            ) : (
              <Rocket className="h-4 w-4 text-[#f15a38]" />
            )}
            <p className="flex-1 text-xs font-semibold">
              {completed
                ? "Проект развёрнут"
                : failed
                  ? "Развёртывание не завершилось"
                  : deployQuery.data?.detail ?? "Запускаем развёртывание…"}
            </p>
            {deployQuery.data?.phase && (
              <Badge
                variant={completed ? "success" : failed ? "danger" : "default"}
                className="text-[10px]"
              >
                {deployQuery.data.phase}
              </Badge>
            )}
          </div>
          {deployQuery.data?.error && (
            <p className="text-[11px] text-danger">{deployQuery.data.error}</p>
          )}
          {completed && deployQuery.data?.prod_url && (
            <a
              href={deployQuery.data.prod_url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 text-xs text-[#c84528] hover:underline"
            >
              Открыть сайт <ExternalLink className="h-3 w-3" />
            </a>
          )}
          {(completed || failed) && (
            <Button
              size="sm"
              variant="ghost"
              className="h-11 text-xs sm:h-7"
              onClick={reset}
            >
              Настроить другой сервер
            </Button>
          )}
        </div>
      )}
    </section>
  );
}

"use client";

/**
 * «Сервер и домен» — BYO-VPS + свой домен в одном диалоге.
 *
 * Раздел 1 «Куда публиковать»: выбор цели деплоя — наш хостинг (по умолчанию)
 * или свой VPS пользователя. Добавление своего сервера (ключ или логин+пароль),
 * проверка подключения, выбор как цели проекта.
 *
 * Раздел 2 «Свой домен»: подключение домена, которым уже владеет пользователь —
 * показываем DNS-инструкцию (A-запись → нужный IP), проверяем запись, выпускаем
 * SSL.
 *
 * Покупка домена через нас — в разработке (нужен договор с регистратором и
 * юрлицо), поэтому здесь только заметка-заглушка, без обещания рабочей оплаты.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Globe, Loader2, Server, ShieldCheck, Trash2 } from "lucide-react";
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
import { Separator } from "@/components/ui/separator";
import { ApiError } from "@/lib/api/client";
import {
  createDeployTarget,
  deleteDeployTarget,
  listDeployTargets,
  setProjectDeployTarget,
  verifyDeployTarget,
  type DeployTarget,
} from "@/lib/api/deploy-targets";
import {
  checkDomain,
  connectDomain,
  deleteDomain,
  issueDomainCert,
  listDomains,
  type CustomDomain,
} from "@/lib/api/domains";
import { getProject } from "@/lib/api/projects";

function errMsg(e: unknown): string {
  return e instanceof ApiError ? e.message : "Что-то пошло не так";
}

export function DeploySettingsButton({ projectId }: { projectId: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => setOpen(true)}
        className="gap-1.5 h-7 px-2.5 text-xs"
        title="Свой сервер и домен для деплоя"
      >
        <Server className="h-3 w-3" />
        <span className="hidden 2xl:inline">Сервер и домен</span>
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-[560px] max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Server className="h-4 w-4" />
              Сервер и домен
            </DialogTitle>
            <DialogDescription>
              Опубликуйте проект на своём сервере и подключите собственный домен —
              или оставьте наш хостинг по умолчанию.
            </DialogDescription>
          </DialogHeader>
          {open && (
            <div className="space-y-6 py-1">
              <DeployTargetSection projectId={projectId} />
              <Separator />
              <DomainSection projectId={projectId} />
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}

// ---------------------------------------------------------------------------
// Раздел 1 — куда публиковать (наш хостинг / свой VPS)
// ---------------------------------------------------------------------------

function DeployTargetSection({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);

  const project = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => getProject(projectId),
  });
  const targets = useQuery({
    queryKey: ["deploy-targets"],
    queryFn: listDeployTargets,
  });

  const currentTargetId = project.data?.deploy_target_id ?? null;

  const selectMut = useMutation({
    mutationFn: (targetId: string | null) =>
      setProjectDeployTarget(projectId, targetId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Цель деплоя обновлена");
    },
    onError: (e) => toast.error("Не удалось выбрать", { description: errMsg(e) }),
  });

  const verifyMut = useMutation({
    mutationFn: (targetId: string) => verifyDeployTarget(targetId),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["deploy-targets"] });
      if (res.ok) toast.success("Сервер доступен", { description: res.detail ?? undefined });
      else toast.error("Проверка не прошла", { description: res.detail ?? undefined });
    },
    onError: (e) => toast.error("Ошибка проверки", { description: errMsg(e) }),
  });

  const deleteMut = useMutation({
    mutationFn: (targetId: string) => deleteDeployTarget(targetId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["deploy-targets"] });
      qc.invalidateQueries({ queryKey: ["project", projectId] });
      toast.success("Сервер удалён");
    },
    onError: (e) => toast.error("Не удалось удалить", { description: errMsg(e) }),
  });

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">Куда публиковать</h3>
        {!adding && (
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setAdding(true)}>
            + Свой сервер
          </Button>
        )}
      </div>

      <div className="space-y-1.5">
        <TargetRow
          label="Наш хостинг"
          sub="Публикация на серверах Omnia (по умолчанию)"
          selected={currentTargetId === null}
          onSelect={() => selectMut.mutate(null)}
          busy={selectMut.isPending}
        />
        {targets.data?.map((t) => (
          <TargetRow
            key={t.id}
            label={t.label}
            sub={`${t.ssh_user}@${t.ssh_host}:${t.ssh_port} · ${t.auth_type === "key" ? "ключ" : "пароль"}`}
            selected={currentTargetId === t.id}
            onSelect={() => selectMut.mutate(t.id)}
            busy={selectMut.isPending}
            status={t.verify_status}
            onVerify={() => verifyMut.mutate(t.id)}
            verifying={verifyMut.isPending && verifyMut.variables === t.id}
            onDelete={() => deleteMut.mutate(t.id)}
            publicKey={t.ssh_public_key}
          />
        ))}
      </div>

      {adding && (
        <AddTargetForm
          onDone={() => {
            setAdding(false);
            qc.invalidateQueries({ queryKey: ["deploy-targets"] });
          }}
          onCancel={() => setAdding(false)}
        />
      )}
    </section>
  );
}

function TargetRow({
  label,
  sub,
  selected,
  onSelect,
  busy,
  status,
  onVerify,
  verifying,
  onDelete,
  publicKey,
}: {
  label: string;
  sub: string;
  selected: boolean;
  onSelect: () => void;
  busy: boolean;
  status?: DeployTarget["verify_status"];
  onVerify?: () => void;
  verifying?: boolean;
  onDelete?: () => void;
  publicKey?: string | null;
}) {
  return (
    <div
      className={`rounded-lg border p-2.5 ${selected ? "border-[#7c5cff] bg-[rgba(124,92,255,0.08)]" : "border-border-subtle"}`}
    >
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onSelect}
          disabled={busy || selected}
          className="flex flex-1 items-center gap-2 text-left"
        >
          <span
            className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${selected ? "border-[#7c5cff] bg-[#7c5cff]" : "border-border-strong"}`}
          >
            {selected && <Check className="h-2.5 w-2.5 text-white" />}
          </span>
          <span className="min-w-0">
            <span className="block text-sm font-medium truncate">{label}</span>
            <span className="block text-xs text-fg-tertiary truncate">{sub}</span>
          </span>
        </button>
        {status && (
          <Badge variant={status === "ok" ? "success" : status === "failed" ? "danger" : "default"} className="text-[10px]">
            {status === "ok" ? "проверен" : status === "failed" ? "ошибка" : "не проверен"}
          </Badge>
        )}
        {onVerify && (
          <Button size="sm" variant="ghost" className="h-6 px-2 text-[11px]" onClick={onVerify} disabled={verifying}>
            {verifying ? <Loader2 className="h-3 w-3 animate-spin" /> : "Проверить"}
          </Button>
        )}
        {onDelete && (
          <button type="button" onClick={onDelete} className="text-fg-tertiary hover:text-red-400" title="Удалить сервер">
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      {publicKey && (
        <div className="mt-2 rounded-md bg-surface-overlay p-2">
          <p className="text-[11px] text-fg-tertiary mb-1">
            Добавьте этот ключ на сервер:{" "}
            <code className="text-fg-secondary">~/.ssh/authorized_keys</code>
          </p>
          <code className="block break-all text-[10px] text-fg-secondary">{publicKey}</code>
        </div>
      )}
    </div>
  );
}

function AddTargetForm({ onDone, onCancel }: { onDone: () => void; onCancel: () => void }) {
  const [label, setLabel] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [user, setUser] = useState("root");
  const [authType, setAuthType] = useState<"key" | "password">("key");
  const [secret, setSecret] = useState("");

  const createMut = useMutation({
    mutationFn: () =>
      createDeployTarget({
        label: label.trim() || host.trim(),
        ssh_host: host.trim(),
        ssh_port: Number(port) || 22,
        ssh_user: user.trim() || "root",
        auth_type: authType,
        secret: secret.trim() || undefined,
      }),
    onSuccess: (t) => {
      if (t.ssh_public_key)
        toast.success("Сервер добавлен", { description: "Добавьте показанный публичный ключ на сервер, затем нажмите «Проверить»." });
      else toast.success("Сервер добавлен", { description: "Нажмите «Проверить» подключение." });
      onDone();
    },
    onError: (e) => toast.error("Не удалось добавить", { description: errMsg(e) }),
  });

  const canSubmit = host.trim() && (authType === "key" || secret.trim());

  return (
    <div className="rounded-lg border border-border-subtle p-3 space-y-2.5">
      <div className="grid grid-cols-2 gap-2">
        <Field label="Название"><Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Мой сервер" className="h-8" /></Field>
        <Field label="Пользователь"><Input value={user} onChange={(e) => setUser(e.target.value)} placeholder="root" className="h-8" /></Field>
        <Field label="Host / IP"><Input value={host} onChange={(e) => setHost(e.target.value)} placeholder="203.0.113.10" className="h-8" /></Field>
        <Field label="SSH-порт"><Input value={port} onChange={(e) => setPort(e.target.value)} placeholder="22" className="h-8" /></Field>
      </div>

      <div>
        <Label className="text-xs mb-1 block">Способ входа</Label>
        <div className="flex gap-1.5">
          <button type="button" onClick={() => setAuthType("key")} className={`flex-1 rounded-md border px-2 py-1.5 text-xs ${authType === "key" ? "border-[#7c5cff] bg-[rgba(124,92,255,0.08)]" : "border-border-subtle text-fg-secondary"}`}>
            SSH-ключ (безопаснее)
          </button>
          <button type="button" onClick={() => setAuthType("password")} className={`flex-1 rounded-md border px-2 py-1.5 text-xs ${authType === "password" ? "border-[#7c5cff] bg-[rgba(124,92,255,0.08)]" : "border-border-subtle text-fg-secondary"}`}>
            Логин + пароль
          </button>
        </div>
      </div>

      {authType === "password" ? (
        <Field label="Пароль SSH">
          <Input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} placeholder="Пароль от сервера" className="h-8" />
        </Field>
      ) : (
        <Field label="Приватный ключ (необязательно)">
          <textarea
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder="Оставьте пустым — мы сгенерируем ключ и покажем публичную часть для добавления на сервер"
            className="w-full rounded-md border border-border-subtle bg-surface-overlay px-2 py-1.5 text-xs h-16 resize-none"
          />
        </Field>
      )}

      <div className="flex justify-end gap-2 pt-1">
        <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={onCancel}>Отмена</Button>
        <Button size="sm" className="h-7 text-xs" disabled={!canSubmit || createMut.isPending} onClick={() => createMut.mutate()}>
          {createMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : "Добавить"}
        </Button>
      </div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Label className="text-xs mb-1 block text-fg-secondary">{label}</Label>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Раздел 2 — свой домен
// ---------------------------------------------------------------------------

function DomainSection({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const [host, setHost] = useState("");

  const domains = useQuery({
    queryKey: ["domains", projectId],
    queryFn: () => listDomains(projectId),
  });

  const connectMut = useMutation({
    mutationFn: () => connectDomain(projectId, host.trim()),
    onSuccess: () => {
      setHost("");
      qc.invalidateQueries({ queryKey: ["domains", projectId] });
      toast.success("Домен добавлен", { description: "Настройте A-запись по инструкции ниже." });
    },
    onError: (e) => toast.error("Не удалось подключить", { description: errMsg(e) }),
  });

  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        <Globe className="h-4 w-4 text-fg-secondary" />
        <h3 className="text-sm font-semibold">Свой домен</h3>
      </div>

      <div className="flex gap-2">
        <Input value={host} onChange={(e) => setHost(e.target.value)} placeholder="shop.example.ru" className="h-8" />
        <Button size="sm" className="h-8 text-xs shrink-0" disabled={!host.trim() || connectMut.isPending} onClick={() => connectMut.mutate()}>
          {connectMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : "Подключить"}
        </Button>
      </div>

      <div className="space-y-2">
        {domains.data?.map((d) => (
          <DomainRow key={d.id} domain={d} projectId={projectId} />
        ))}
      </div>

      <p className="text-[11px] text-fg-tertiary leading-relaxed">
        Покупка домена прямо у нас (заказ + оплата в рублях, авто-настройка) — в
        разработке. Пока подключите домен, который у вас уже есть.
      </p>
    </section>
  );
}

function DomainRow({ domain, projectId }: { domain: CustomDomain; projectId: string }) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["domains", projectId] });

  const checkMut = useMutation({
    mutationFn: () => checkDomain(domain.id),
    onSuccess: (d) => { invalidate(); toast.message(d.dns_status === "ok" ? "DNS настроен" : "Проверено", { description: d.last_detail ?? undefined }); },
    onError: (e) => toast.error("Ошибка проверки", { description: errMsg(e) }),
  });
  const issueMut = useMutation({
    mutationFn: () => issueDomainCert(domain.id),
    onSuccess: (d) => { invalidate(); toast.success(d.cert_status === "active" ? "SSL выпущен" : "Готово", { description: d.last_detail ?? undefined }); },
    onError: (e) => toast.error("Не удалось выпустить SSL", { description: errMsg(e) }),
  });
  const deleteMut = useMutation({
    mutationFn: () => deleteDomain(domain.id),
    onSuccess: () => { invalidate(); toast.success("Домен убран"); },
    onError: (e) => toast.error("Не удалось убрать", { description: errMsg(e) }),
  });

  const dnsOk = domain.dns_status === "ok";
  const certActive = domain.cert_status === "active";

  return (
    <div className="rounded-lg border border-border-subtle p-2.5 space-y-2">
      <div className="flex items-center gap-2">
        <span className="flex-1 min-w-0">
          <span className="block text-sm font-medium truncate">{domain.host}</span>
        </span>
        {certActive ? (
          <a href={`https://${domain.host}`} target="_blank" rel="noreferrer" className="text-xs text-[#7c5cff] hover:underline flex items-center gap-1">
            <ShieldCheck className="h-3.5 w-3.5" /> открыть
          </a>
        ) : (
          <Badge variant={dnsOk ? "success" : "default"} className="text-[10px]">
            {dnsOk ? "DNS ок" : "ждём DNS"}
          </Badge>
        )}
        <button type="button" onClick={() => deleteMut.mutate()} className="text-fg-tertiary hover:text-red-400" title="Убрать домен">
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      </div>

      {!certActive && domain.dns_instructions && (
        <p className="text-[11px] text-fg-tertiary leading-relaxed">{domain.dns_instructions}</p>
      )}
      {domain.last_detail && !certActive && (
        <p className="text-[11px] text-fg-secondary">{domain.last_detail}</p>
      )}

      {!certActive && (
        <div className="flex gap-2">
          <Button size="sm" variant="ghost" className="h-6 px-2 text-[11px]" onClick={() => checkMut.mutate()} disabled={checkMut.isPending}>
            {checkMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : "Проверить DNS"}
          </Button>
          <Button size="sm" className="h-6 px-2 text-[11px]" onClick={() => issueMut.mutate()} disabled={!dnsOk || issueMut.isPending}>
            {issueMut.isPending ? <Loader2 className="h-3 w-3 animate-spin" /> : "Выпустить SSL"}
          </Button>
        </div>
      )}
    </div>
  );
}

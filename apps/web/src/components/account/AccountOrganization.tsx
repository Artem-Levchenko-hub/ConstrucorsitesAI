"use client";
import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Building2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { getMaxAccess, saveBusinessProfile, type BusinessProfile, type BusinessKind } from "@/lib/api/max-account";
import { QueryError } from "./account-presentation";
const statuses = { verified: ["Проверена", "success"], pending: ["На проверке", "pending"], rejected: ["Отклонена", "error"], suspended: ["Приостановлена", "error"] } as const;
export function AccountOrganization() {
  const access = useQuery({ queryKey: ["max-access"], queryFn: getMaxAccess });
  return <>
    <QueryError error={access.error} retry={() => void access.refetch()} />
    {access.isPending && <p role="status">Загружаем организацию…</p>}
    {access.data && <>
      <section className="account-org-summary">
        <Building2 className="size-8" />
        <div>
          <span className="account-hint">Владелец MAX-приложений</span>
          <h2>{access.data.business?.legal_name || "Добавьте организацию"}</h2>
          {access.data.business && <span className={`account-status ${statuses[access.data.business.status][1]}`}>{statuses[access.data.business.status][0]}</span>}</div>
      </section>
      {!access.data.email_verified && <div className="account-notice pending">
        <p>Сначала подтвердите email аккаунта.</p>
        <Link href="/max/onboarding">Подтвердить email →</Link>
      </div>}
      <OrganizationForm key={access.data.business?.id ?? "new"} business={access.data.business} />
    </>}
  </>;
}
function OrganizationForm({ business }: { business: BusinessProfile | null }) {
  const client = useQueryClient();
  const locked = business?.status === "verified";
  const [kind, setKind] = useState<BusinessKind>(business?.kind ?? "legal_entity");
  const [name, setName] = useState(business?.legal_name ?? "");
  const [inn, setInn] = useState(business?.inn ?? "");
  const [ogrn, setOgrn] = useState(business?.ogrn ?? "");
  const save = useMutation({ mutationFn: saveBusinessProfile, onSuccess: () => client.invalidateQueries({ queryKey: ["max-access"] }) });
  return <form className="account-panel account-org-form" onSubmit={e => { e.preventDefault(); if (!locked) save.mutate({ kind, legal_name: name.trim(), inn: inn.trim(), ogrn: ogrn.trim() || undefined }); }}>
    <div>
      <h2>Реквизиты</h2>
      <label htmlFor="org-kind">Тип организации</label>
      <select id="org-kind" value={kind} disabled={locked} onChange={e => setKind(e.target.value as BusinessKind)}>
        <option value="legal_entity">Юридическое лицо</option>
        <option value="sole_proprietor">Индивидуальный предприниматель</option>
        <option value="self_employed">Самозанятый</option>
      </select>
      <label htmlFor="org-name">Наименование / ФИО</label>
      <Input id="org-name" value={name} readOnly={locked} onChange={e => setName(e.target.value)} required />
      <label htmlFor="org-inn">ИНН</label>
      <Input id="org-inn" inputMode="numeric" pattern={kind === "legal_entity" ? "[0-9]{10}" : "[0-9]{12}"} value={inn} readOnly={locked} onChange={e => setInn(e.target.value)} required />
      <label htmlFor="org-ogrn">ОГРН / ОГРНИП{kind === "self_employed" ? " (необязательно)" : ""}</label>
      <Input id="org-ogrn" value={ogrn} readOnly={locked} required={kind !== "self_employed"} pattern={kind === "legal_entity" ? "[0-9]{13}" : kind === "sole_proprietor" ? "[0-9]{15}" : undefined} onChange={e => setOgrn(e.target.value)} inputMode="numeric" />
      {!locked && <Button type="submit" disabled={save.isPending || !name.trim() || !inn.trim()}>{save.isPending ? "Сохраняем…" : "Сохранить реквизиты"}</Button>}
      <QueryError error={save.error} />{save.isSuccess && <p role="status">Реквизиты сохранены. Статус проверки получен с сервера.</p>}</div>
    <aside>
      <h2>Проверка организации</h2>
      <p>{locked ? "Подтверждённый бизнес-профиль меняется через поддержку." : "После сохранения сервер проверит данные и вернёт актуальный статус."}</p>
      {business?.verification_note && <div className="account-notice pending">{business.verification_note}</div>}
      <p className="account-hint">Эти реквизиты используются в MAX Studio.</p>
    </aside>
  </form>;
}

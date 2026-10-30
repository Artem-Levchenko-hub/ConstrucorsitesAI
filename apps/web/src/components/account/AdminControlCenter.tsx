"use client";
import { useEffect, useId, useRef, useState } from "react";
import { History, UsersRound, MessageCircle, Settings } from "lucide-react";
import { AdminAuditPanel } from "./AdminAuditPanel";
import { AdminUsersPanel } from "./AdminUsersPanel";
import { AdminSupportPanel } from "./AdminSupportPanel";
import { AccountPageTransition } from "./AccountPageTransition";
import "./admin.css";

const tabs = [["users", "Аккаунты", UsersRound], ["audit", "Журнал", History], ["support", "Поддержка", MessageCircle], ["settings", "Настройки", Settings]] as const;
type Tab = (typeof tabs)[number][0];
type Location = { tab: Tab; archive: boolean; ticket: string | null };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
function readLocation(): Location {
  const params = new URLSearchParams(window.location.search);
  const value = params.get("tab");
  const tab = tabs.some(([id]) => id === value) ? value as Tab : "users";
  const ticket = params.get("ticket");
  return { tab, archive: tab === "settings" && params.get("section") === "support-archive", ticket: ticket && uuid.test(ticket) ? ticket : null };
}
export function AdminControlCenter({ currentEmail }: { currentEmail: string }) {
  const [location, setLocation] = useState<Location>({ tab: "users", archive: false, ticket: null });
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const id = useId();
  useEffect(() => { const sync = () => setLocation(readLocation()); sync(); window.addEventListener("popstate", sync); return () => window.removeEventListener("popstate", sync); }, []);
  function navigate(next: Location) {
    const url = new URL(window.location.href); url.searchParams.set("tab", next.tab);
    if (next.archive) url.searchParams.set("section", "support-archive"); else url.searchParams.delete("section");
    if (next.ticket) url.searchParams.set("ticket", next.ticket); else url.searchParams.delete("ticket");
    window.history.pushState(null, "", `${url.pathname}${url.search}${url.hash}`); setLocation(next);
  }
  function selectTab(tab: Tab) { navigate({ tab, archive: false, ticket: null }); }
  return <div className="admin-center">
    <div role="tablist" aria-label="Разделы админ-центра" className="admin-tabs">
      {tabs.map(([value, label, Icon], index) => <button key={value} ref={node => { tabRefs.current[index] = node; }} type="button" role="tab" id={`${id}-${value}`} aria-selected={location.tab === value} aria-controls={`${id}-panel`} tabIndex={location.tab === value ? 0 : -1} onClick={() => selectTab(value)} onKeyDown={event => {
        const next = event.key === "ArrowRight" ? (index + 1) % tabs.length : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
        if (next === null) return; event.preventDefault(); selectTab(tabs[next][0]); tabRefs.current[next]?.focus();
      }}><Icon aria-hidden="true" />{label}</button>)}
    </div>
    <section role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${location.tab}`} tabIndex={0}>
      <AccountPageTransition transitionKey={`${location.tab}:${location.archive}:${location.ticket ?? ""}`}>
      {location.tab === "users" && <AdminUsersPanel currentEmail={currentEmail} />}
      {location.tab === "audit" && <AdminAuditPanel />}
      {location.tab === "support" && <AdminSupportPanel ticketId={location.ticket} onSelect={ticket => navigate({ ...location, ticket })} />}
      {location.tab === "settings" && (location.archive ? <><button type="button" className="support-back" onClick={() => selectTab("settings")}>← Настройки</button><AdminSupportPanel archived ticketId={location.ticket} onSelect={ticket => navigate({ ...location, ticket })} /></> : <div className="admin-support"><h2>Настройки</h2><button aria-label="Архив поддержки" className="support-ticket" type="button" onClick={() => navigate({ tab: "settings", archive: true, ticket: null })}><strong>Архив поддержки</strong><span className="support-meta">Завершённые более суток назад обращения</span></button></div>)}
      </AccountPageTransition>
    </section>
  </div>;
}

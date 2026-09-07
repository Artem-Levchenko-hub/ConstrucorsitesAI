"use client";

import { useId, useRef, useState } from "react";
import { Building2, History, UsersRound } from "lucide-react";
import { AdminAuditPanel } from "./AdminAuditPanel";
import { AdminUsersPanel } from "./AdminUsersPanel";
import { AdminVerificationPanel } from "./AdminVerificationPanel";
import "./admin.css";

const tabs = [
  ["users", "Аккаунты", UsersRound],
  ["businesses", "Организации", Building2],
  ["audit", "Журнал", History],
] as const;

export function AdminControlCenter({ currentEmail }: { currentEmail: string }) {
  const [tab, setTab] = useState<(typeof tabs)[number][0]>("users");
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const id = useId();
  return (
    <div className="admin-center">
      <div role="tablist" aria-label="Разделы админ-центра" className="admin-tabs">
        {tabs.map(([value, label, Icon], index) => (
          <button key={value} ref={node => { tabRefs.current[index] = node; }} type="button"
            role="tab" id={`${id}-${value}`} aria-selected={tab === value}
            aria-controls={`${id}-panel`} tabIndex={tab === value ? 0 : -1}
            onClick={() => setTab(value)}
            onKeyDown={event => {
              const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
                : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
                  : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : null;
              if (next === null) return;
              event.preventDefault(); setTab(tabs[next][0]); tabRefs.current[next]?.focus();
            }}>
            <Icon aria-hidden="true" />{label}
          </button>
        ))}
      </div>
      <section role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-${tab}`} tabIndex={0}>
        {tab === "users" && <AdminUsersPanel currentEmail={currentEmail} />}
        {tab === "businesses" && <AdminVerificationPanel />}
        {tab === "audit" && <AdminAuditPanel />}
      </section>
    </div>
  );
}

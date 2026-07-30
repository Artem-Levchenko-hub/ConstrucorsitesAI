"use client";

import { useState } from "react";
import { Building2, History, UsersRound } from "lucide-react";

import { AdminAuditPanel } from "@/components/account/AdminAuditPanel";
import { AdminUsersPanel } from "@/components/account/AdminUsersPanel";
import { AdminVerificationPanel } from "@/components/account/AdminVerificationPanel";
import { cn } from "@/lib/utils";

const tabs = [
  ["users", "Аккаунты", UsersRound],
  ["businesses", "Организации", Building2],
  ["audit", "Журнал", History],
] as const;

export function AdminControlCenter({
  currentEmail,
}: {
  currentEmail: string;
}) {
  const [tab, setTab] = useState<(typeof tabs)[number][0]>("users");

  return (
    <div className="space-y-5">
      <div className="flex gap-2 overflow-x-auto rounded-[12px] border border-[#d8d4cb] bg-[#fcfbf7] p-2">
        {tabs.map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={cn(
              "flex h-10 shrink-0 items-center gap-2 rounded-[8px] px-4 text-xs font-medium",
              tab === id
                ? "bg-[#171716] text-white"
                : "text-[#6d6962] hover:bg-[#ece8df]",
            )}
          >
            <Icon className="size-4" />
            {label}
          </button>
        ))}
      </div>
      {tab === "users" && <AdminUsersPanel currentEmail={currentEmail} />}
      {tab === "businesses" && <AdminVerificationPanel />}
      {tab === "audit" && <AdminAuditPanel />}
    </div>
  );
}

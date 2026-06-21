"use client";

import * as React from "react";

import { users as usersSdk, type DirectoryUser } from "@/lib/sdk";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// Module-level cache so many pickers / name-labels on one page share ONE fetch
// (a list of 50 messages rendering <UserName> must not make 50 requests).
let _cache: Promise<DirectoryUser[]> | null = null;
function loadDirectory(): Promise<DirectoryUser[]> {
  if (!_cache) _cache = usersSdk.directory().catch(() => []);
  return _cache;
}

/** The registered people (optionally filtered by role), or null while loading. */
export function useDirectory(role?: string): DirectoryUser[] | null {
  const [list, setList] = React.useState<DirectoryUser[] | null>(null);
  React.useEffect(() => {
    let on = true;
    loadDirectory().then((u) => {
      if (on) setList(role ? u.filter((x) => x.role === role) : u);
    });
    return () => {
      on = false;
    };
  }, [role]);
  return list;
}

/**
 * `<UserSelect>` — pick a REAL registered person (by id). Use it anywhere a form
 * references a human: a message recipient, a task assignee, a class tutor. Store
 * the chosen `value` (a user-id string) in a plain `string` field — NOT a
 * reference to a `User` entity (there is none; people are auth accounts). This
 * is what makes «написать сообщение» / «назначить ответственного» actually work —
 * you can finally find the other person. Optionally `role`-filter (only teachers).
 *
 *   <UserSelect value={form.receiverId} onChange={(id) => set("receiverId", id)}
 *               role="teacher" placeholder="Кому" />
 *
 * Fixed kit component — import and compose, don't edit.
 */
export function UserSelect({
  value,
  onChange,
  role,
  placeholder = "Выберите человека",
  disabled,
}: {
  value?: string;
  onChange: (id: string) => void;
  role?: string;
  placeholder?: string;
  disabled?: boolean;
}) {
  const list = useDirectory(role);
  return (
    <Select
      value={value || undefined}
      onValueChange={onChange}
      disabled={disabled || list === null}
    >
      <SelectTrigger>
        <SelectValue placeholder={list === null ? "Загрузка…" : placeholder} />
      </SelectTrigger>
      <SelectContent>
        {(list ?? []).length === 0 ? (
          <div className="px-2 py-3 text-center text-xs text-muted-foreground">
            Пока некого выбрать — пригласите коллег зарегистрироваться.
          </div>
        ) : (
          (list ?? []).map((u) => (
            <SelectItem key={u.id} value={u.id}>
              {u.name}
            </SelectItem>
          ))
        )}
      </SelectContent>
    </Select>
  );
}

/**
 * `<UserName>` — render a person's name from their stored id, so a saved
 * `senderId` / `receiverId` / `assigneeId` shows a human name instead of a uuid.
 * Shares the cached directory with `<UserSelect>`.
 */
export function UserName({
  id,
  fallback = "—",
}: {
  id?: string | null;
  fallback?: string;
}) {
  const list = useDirectory();
  if (!id) return <>{fallback}</>;
  const u = list?.find((x) => x.id === id);
  return <>{u ? u.name : list === null ? "…" : fallback}</>;
}

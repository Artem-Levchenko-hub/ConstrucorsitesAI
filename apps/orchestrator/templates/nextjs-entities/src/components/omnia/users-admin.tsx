"use client";

import * as React from "react";

import { admin, type AdminUser } from "@/lib/sdk";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export interface UsersAdminRole {
  value: string;
  label: string;
}

// `admin` is always offered so the operator role can be (re)assigned.
const DEFAULT_ROLES: UsersAdminRole[] = [
  { value: "admin", label: "Администратор" },
  { value: "user", label: "Пользователь" },
];

/**
 * `<UsersAdmin>` — the managed user-administration screen. Lists the app's REAL
 * registered accounts (the auth users — everyone who actually signed up) and
 * lets an admin assign each one a role. Backed by the fixed `admin.listUsers()`
 * / `admin.setUserRole()` SDK (admin-only API; the first signup is admin).
 *
 * Use this for ANY «Пользователи» / «Команда» / role-management screen.
 * NEVER model people as an entity (`entities/User.json`): signups land in the
 * auth users table, not the `records` table, so a User entity always renders
 * empty and registered users (your colleagues) stay invisible. This component
 * reads the correct source. Pass the app's own role vocabulary:
 *
 *   <UsersAdmin roles={[
 *     { value: "teacher", label: "Учитель" },
 *     { value: "parent",  label: "Родитель" },
 *     { value: "student", label: "Ученик" },
 *     { value: "admin",   label: "Администратор" },
 *   ]} />
 *
 * Fixed kit component — import and compose, don't edit.
 */
export function UsersAdmin({
  roles = DEFAULT_ROLES,
  title = "Пользователи",
  description = "Все, кто зарегистрировался в приложении. Назначьте каждому роль.",
}: {
  roles?: UsersAdminRole[];
  title?: string;
  description?: string;
}) {
  const [users, setUsers] = React.useState<AdminUser[] | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [savingId, setSavingId] = React.useState<string | null>(null);

  React.useEffect(() => {
    admin
      .listUsers()
      .then(setUsers)
      .catch((e: unknown) =>
        setError(
          e instanceof Error ? e.message : "Не удалось загрузить пользователей",
        ),
      );
  }, []);

  const onRole = async (id: string, role: string) => {
    setSavingId(id);
    setUsers((prev) => prev?.map((u) => (u.id === id ? { ...u, role } : u)) ?? prev);
    try {
      await admin.setUserRole(id, role);
    } catch {
      // Reload the truth if the server rejected the change (e.g. self-demotion).
      admin.listUsers().then(setUsers).catch(() => {});
    } finally {
      setSavingId(null);
    }
  };

  const roleLabel = (r: string) => roles.find((x) => x.value === r)?.label ?? r;

  return (
    <Card className="overflow-hidden p-0">
      <div className="border-b border-border px-5 py-4">
        <h2 className="text-base font-semibold text-foreground">{title}</h2>
        <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>
      </div>

      {error ? (
        <div className="px-5 py-10 text-center text-sm text-muted-foreground">
          {error}
        </div>
      ) : users === null ? (
        <div className="space-y-2 p-5">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-10 w-full" />
          ))}
        </div>
      ) : users.length === 0 ? (
        <div className="px-5 py-10 text-center text-sm text-muted-foreground">
          Пока никто не зарегистрировался. Поделитесь ссылкой на приложение — новые
          аккаунты появятся здесь.
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>ФИО</TableHead>
              <TableHead>Email</TableHead>
              <TableHead className="w-[200px]">Роль</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.map((u) => (
              <TableRow key={u.id}>
                <TableCell className="font-medium">
                  {u.name?.trim() || "—"}
                </TableCell>
                <TableCell className="text-muted-foreground">{u.email}</TableCell>
                <TableCell>
                  <Select
                    value={u.role}
                    onValueChange={(v) => onRole(u.id, v)}
                    disabled={savingId === u.id}
                  >
                    <SelectTrigger className="h-8 w-[180px]">
                      <SelectValue>{roleLabel(u.role)}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      {roles.map((r) => (
                        <SelectItem key={r.value} value={r.value}>
                          {r.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}

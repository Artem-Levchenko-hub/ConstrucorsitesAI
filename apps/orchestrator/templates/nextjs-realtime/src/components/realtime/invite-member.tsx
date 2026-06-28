"use client";

/**
 * InviteMember — FIXED realtime primitive. Renders an email input that invites
 * an existing user into a channel (POST /api/channels/<id>/members) and shows
 * the current roster (GET /api/channels/<id>/members).
 *
 * It is FIXED so a UI restyle can NEVER drop the "add a friend" affordance —
 * without it a generated chat leaves the creator alone in every conversation
 * with no way to add anyone (the live owner bug). Import and render
 * `<InviteMember channelId={id} />` inside a channel view; do not reimplement
 * invite by hand.
 */

import { useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Member = {
  userId: string;
  email: string;
  name: string | null;
  role: string;
};

export function InviteMember({ channelId }: { channelId: string }) {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [members, setMembers] = useState<Member[]>([]);

  const loadMembers = useCallback(async () => {
    if (!channelId) return;
    try {
      const res = await fetch(`/api/channels/${channelId}/members`, {
        credentials: "include",
      });
      if (!res.ok) return;
      const { data } = (await res.json()) as { data?: Member[] };
      setMembers(data ?? []);
    } catch {
      /* roster is best-effort decoration — never block the invite on it */
    }
  }, [channelId]);

  useEffect(() => {
    void loadMembers();
  }, [loadMembers]);

  async function invite(e: React.FormEvent) {
    e.preventDefault();
    const value = email.trim();
    if (!value || busy) return;
    setBusy(true);
    setStatus(null);
    try {
      const res = await fetch(`/api/channels/${channelId}/members`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: value }),
        credentials: "include",
      });
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      if (res.ok) {
        setStatus(`Добавлен: ${value}`);
        setEmail("");
        void loadMembers();
      } else {
        setStatus(body.error ?? "Не удалось добавить участника");
      }
    } catch {
      setStatus("Сеть недоступна — попробуйте ещё раз");
    } finally {
      setBusy(false);
      setTimeout(() => setStatus(null), 4000);
    }
  }

  return (
    <div className="space-y-2">
      <form onSubmit={invite} className="flex items-center gap-2">
        <Input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Добавить участника по email"
          aria-label="Email участника"
          className="min-w-0 flex-1"
        />
        <Button type="submit" disabled={busy || !email.trim()}>
          {busy ? "Добавляю…" : "Пригласить"}
        </Button>
      </form>
      {members.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {members.map((m) => (
            <li
              key={m.userId}
              className="rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground"
              title={m.email}
            >
              {m.name?.trim() || m.email}
              {m.role === "admin" ? " · админ" : ""}
            </li>
          ))}
        </ul>
      )}
      {status && (
        <p className="text-xs text-muted-foreground" role="status">
          {status}
        </p>
      )}
    </div>
  );
}

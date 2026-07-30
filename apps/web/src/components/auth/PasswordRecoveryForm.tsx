"use client";

import { FormEvent, useState } from "react";
import Link from "next/link";
import { Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { apiFetch } from "@/lib/api/client";

export function PasswordRecoveryForm() {
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    try {
      await apiFetch("/api/auth/password/forgot", {
        method: "POST",
        json: { email },
      });
      setMessage("Если аккаунт существует, письмо со ссылкой отправлено.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Не удалось отправить письмо");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="recovery-email">Email</Label>
        <Input
          id="recovery-email"
          type="email"
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          required
          autoComplete="email"
        />
      </div>
      {message && <p className="text-sm text-fg-secondary">{message}</p>}
      <Button className="w-full" disabled={pending}>
        {pending && <Loader2 className="mr-2 size-4 animate-spin" />}
        Отправить ссылку
      </Button>
      <p className="text-center text-xs text-fg-tertiary">
        <Link className="text-accent hover:underline" href="/login">
          Вернуться ко входу
        </Link>
      </p>
    </form>
  );
}

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";
import { useState } from "react";

export default function SignUpPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const res = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, email, password }),
    });
    if (!res.ok) {
      const body = (await res.json().catch(() => ({}))) as { error?: string };
      setLoading(false);
      setError(body.error ?? "Не удалось зарегистрироваться");
      return;
    }
    const signedIn = await signIn("credentials", {
      email,
      password,
      redirect: false,
    });
    setLoading(false);
    if (!signedIn || signedIn.error) {
      router.push("/signin");
      return;
    }
    router.push("/chat");
    router.refresh();
  }

  return (
    <div className="app-canvas flex min-h-screen items-center justify-center p-6">
      <form
        onSubmit={onSubmit}
        className="fade-up w-full max-w-sm space-y-4 rounded-2xl border border-border bg-card/80 p-7 elev-2 backdrop-blur-sm"
      >
        <div className="mb-1 flex flex-col items-center gap-2 text-center">
          <span className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary text-primary-foreground text-lg font-bold shadow-md">
            O
          </span>
          <h1 className="text-xl font-bold tracking-tight">Создать аккаунт</h1>
          <p className="text-sm text-muted-foreground">Пара секунд — и вы в сети</p>
        </div>
        <input
          type="text"
          placeholder="Имя"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full rounded-lg border border-border bg-background px-3.5 py-2.5 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/30"
        />
        <input
          type="email"
          required
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-lg border border-border bg-background px-3.5 py-2.5 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/30"
        />
        <input
          type="password"
          required
          minLength={8}
          placeholder="Пароль (минимум 8 символов)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-lg border border-border bg-background px-3.5 py-2.5 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/30"
        />
        {error && <p className="text-sm text-destructive">{error}</p>}
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-primary px-3 py-2.5 text-sm font-semibold text-primary-foreground transition hover:opacity-90 active:scale-[.98] disabled:opacity-60"
        >
          {loading ? "Создаём…" : "Создать аккаунт"}
        </button>
        <p className="text-center text-sm text-muted-foreground">
          Уже есть аккаунт?{" "}
          <Link href="/signin" className="font-medium text-primary hover:underline">
            Вход
          </Link>
        </p>
      </form>
    </div>
  );
}

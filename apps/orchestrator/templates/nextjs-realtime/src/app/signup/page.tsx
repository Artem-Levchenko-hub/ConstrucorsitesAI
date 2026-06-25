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
    <div className="flex min-h-screen items-center justify-center p-6">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm space-y-4 rounded-2xl border border-neutral-800 bg-neutral-900/50 p-6"
      >
        <h1 className="text-lg font-semibold">Регистрация</h1>
        <input
          type="text"
          placeholder="Имя"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm outline-none focus:border-neutral-500"
        />
        <input
          type="email"
          required
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="w-full rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm outline-none focus:border-neutral-500"
        />
        <input
          type="password"
          required
          minLength={8}
          placeholder="Пароль (минимум 8 символов)"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="w-full rounded-md border border-neutral-700 bg-neutral-950 px-3 py-2 text-sm outline-none focus:border-neutral-500"
        />
        {error && <p className="text-sm text-red-400">{error}</p>}
        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-md bg-white px-3 py-2 text-sm font-medium text-neutral-900 transition hover:bg-neutral-200 disabled:opacity-60"
        >
          {loading ? "Создаём…" : "Создать аккаунт"}
        </button>
        <p className="text-center text-sm text-neutral-400">
          Уже есть аккаунт?{" "}
          <Link href="/signin" className="text-white underline">
            Вход
          </Link>
        </p>
      </form>
    </div>
  );
}

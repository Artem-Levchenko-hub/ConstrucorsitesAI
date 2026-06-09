/**
 * Pre-built sign-up page. Creates a fresh row in `users` with a bcrypt
 * password hash, then immediately signs the user in so they land on the
 * intended page (`next` param) authenticated.
 *
 * Race-safety: the unique constraint on `users.email` is the source of
 * truth — even if two browsers POST simultaneously, only one row lands.
 * The second sees a Postgres unique-violation and we surface a friendly
 * "email уже зарегистрирован" message.
 */

import { eq } from "drizzle-orm";
import { AuthError } from "next-auth";
import Link from "next/link";
import { redirect } from "next/navigation";
import { auth, hashPassword, signIn } from "@/lib/auth";
import { db } from "@/lib/db";
import { users } from "@/lib/db/schema";

export const metadata = { title: "Регистрация" };

export default async function SignUpPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const sp = await searchParams;
  const session = await auth();
  if (session?.user) redirect(sp.next ?? "/");

  async function action(formData: FormData) {
    "use server";
    const email = String(formData.get("email") ?? "").trim().toLowerCase();
    const password = String(formData.get("password") ?? "");
    const name = String(formData.get("name") ?? "").trim() || null;
    const next = String(formData.get("next") ?? "/");

    if (!email || !password || password.length < 8) {
      redirect(`/signup?error=invalid&next=${encodeURIComponent(next)}`);
    }

    // Insert-or-fail. The unique index on `email` enforces atomicity —
    // a parallel signup will throw here and we redirect to error state.
    try {
      await db.insert(users).values({
        email,
        name,
        passwordHash: await hashPassword(password),
      });
    } catch {
      // Most likely cause is the unique constraint; either way we don't
      // expose the exact error to a public form.
      const exists = await db
        .select({ id: users.id })
        .from(users)
        .where(eq(users.email, email))
        .limit(1);
      if (exists[0]) {
        redirect(`/signup?error=exists&next=${encodeURIComponent(next)}`);
      }
      redirect(`/signup?error=unknown&next=${encodeURIComponent(next)}`);
    }

    // Immediately sign in. `redirectTo` triggers a server-side navigation
    // through Auth.js — survives the action -> response boundary cleanly.
    // If sign-in somehow fails right after the account was created, an
    // AuthError lands the user on /signin instead of crashing; the success
    // path throws NEXT_REDIRECT (not an AuthError) and is re-thrown.
    try {
      await signIn("credentials", { email, password, redirectTo: next });
    } catch (error) {
      if (error instanceof AuthError) {
        redirect(`/signin?next=${encodeURIComponent(next)}`);
      }
      throw error;
    }
  }

  const errorMessage: Record<string, string> = {
    invalid: "Заполните email и пароль (минимум 8 символов).",
    exists: "Этот email уже зарегистрирован. Войдите в существующий аккаунт.",
    unknown: "Что-то пошло не так. Попробуйте ещё раз.",
  };

  return (
    <main className="min-h-screen flex items-center justify-center px-6 py-12">
      <div className="w-full max-w-sm space-y-6">
        <header className="text-center space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Регистрация</h1>
          <p className="text-sm text-zinc-500">
            Уже есть аккаунт?{" "}
            <Link href="/signin" className="text-emerald-700 hover:underline">
              Войти
            </Link>
          </p>
        </header>

        {sp.error && errorMessage[sp.error] && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {errorMessage[sp.error]}
          </div>
        )}

        <form action={action} className="space-y-4">
          <input type="hidden" name="next" value={sp.next ?? "/"} />
          <label className="block">
            <span className="text-sm font-medium">Имя (необязательно)</span>
            <input
              name="name"
              type="text"
              autoComplete="name"
              className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium">Email</span>
            <input
              name="email"
              type="email"
              required
              autoComplete="email"
              className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
            />
          </label>
          <label className="block">
            <span className="text-sm font-medium">Пароль</span>
            <input
              name="password"
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
              className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
            />
            <span className="mt-1 block text-xs text-zinc-500">
              Минимум 8 символов
            </span>
          </label>
          <button
            type="submit"
            className="w-full rounded-lg bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-700"
          >
            Создать аккаунт
          </button>
        </form>
      </div>
    </main>
  );
}

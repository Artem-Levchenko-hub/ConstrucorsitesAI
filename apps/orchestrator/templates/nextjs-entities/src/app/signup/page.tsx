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
import { auth, hashPassword, roleForNewUser, signIn } from "@/lib/auth";
import { APP_HOME } from "@/lib/session";
import { db } from "@/lib/db";
import { users } from "@/lib/db/schema";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export const metadata = { title: "Регистрация" };

export default async function SignUpPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const sp = await searchParams;
  const session = await auth();
  if (session?.user) redirect(sp.next ?? APP_HOME);

  async function action(formData: FormData) {
    "use server";
    const email = String(formData.get("email") ?? "").trim().toLowerCase();
    const password = String(formData.get("password") ?? "");
    const name = String(formData.get("name") ?? "").trim() || null;
    const next = String(formData.get("next") ?? APP_HOME);

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
        role: await roleForNewUser(),
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
    <main className="grid min-h-screen place-items-center bg-muted/30 px-4 py-12">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-card p-8 shadow-sm">
        <header className="mb-6 space-y-1 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">Регистрация</h1>
          <p className="text-sm text-muted-foreground">
            Уже есть аккаунт?{" "}
            <Link href="/signin" className="font-medium text-primary hover:underline">
              Войти
            </Link>
          </p>
        </header>

        {sp.error && errorMessage[sp.error] && (
          <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            {errorMessage[sp.error]}
          </div>
        )}

        <form action={action} className="space-y-4">
          <input type="hidden" name="next" value={sp.next ?? APP_HOME} />
          <div className="space-y-2">
            <label htmlFor="name" className="text-sm font-medium">
              Имя <span className="font-normal text-muted-foreground">(необязательно)</span>
            </label>
            <Input id="name" name="name" type="text" autoComplete="name" />
          </div>
          <div className="space-y-2">
            <label htmlFor="email" className="text-sm font-medium">
              Email
            </label>
            <Input id="email" name="email" type="email" required autoComplete="email" />
          </div>
          <div className="space-y-2">
            <label htmlFor="password" className="text-sm font-medium">
              Пароль
            </label>
            <Input
              id="password"
              name="password"
              type="password"
              required
              minLength={8}
              autoComplete="new-password"
            />
            <p className="text-xs text-muted-foreground">Минимум 8 символов</p>
          </div>
          <Button type="submit" size="lg" className="w-full">
            Создать аккаунт
          </Button>
        </form>
      </div>
    </main>
  );
}

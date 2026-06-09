/**
 * Pre-built sign-in page. AI should NOT regenerate this — the Auth.js
 * Credentials provider routes here on failed auth + this page calls
 * `signIn("credentials", ...)` from a server action. Restyling is fine
 * (Tailwind classes, copy, brand colors); core form structure isn't.
 */

import { AuthError } from "next-auth";
import Link from "next/link";
import { redirect } from "next/navigation";
import { signIn, auth } from "@/lib/auth";

export const metadata = { title: "Вход" };

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const sp = await searchParams;
  // Already signed in → bounce straight through to next= or home.
  const session = await auth();
  if (session?.user) redirect(sp.next ?? "/");

  async function action(formData: FormData) {
    "use server";
    const email = String(formData.get("email") ?? "").trim().toLowerCase();
    const password = String(formData.get("password") ?? "");
    const next = String(formData.get("next") ?? "/");
    try {
      await signIn("credentials", { email, password, redirectTo: next });
    } catch (error) {
      // Bad credentials → Auth.js throws CredentialsSignin (an AuthError).
      // Redirect back to the form with a friendly ?error= banner instead of
      // letting the action crash the whole app. The SUCCESS path throws
      // NEXT_REDIRECT (not an AuthError), so we re-throw it for Next to follow.
      if (error instanceof AuthError) {
        redirect(
          `/signin?error=CredentialsSignin&next=${encodeURIComponent(next)}`,
        );
      }
      throw error;
    }
  }

  return (
    <main className="min-h-screen flex items-center justify-center px-6 py-12">
      <div className="w-full max-w-sm space-y-6">
        <header className="text-center space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Вход</h1>
          <p className="text-sm text-zinc-500">
            Нет аккаунта?{" "}
            <Link href="/signup" className="text-emerald-700 hover:underline">
              Зарегистрироваться
            </Link>
          </p>
        </header>

        {sp.error && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            Неверный email или пароль.
          </div>
        )}

        <form action={action} className="space-y-4">
          <input type="hidden" name="next" value={sp.next ?? "/"} />
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
              autoComplete="current-password"
              className="mt-1 w-full rounded-lg border border-zinc-300 px-3 py-2 text-sm focus:border-emerald-500 focus:outline-none focus:ring-1 focus:ring-emerald-500"
            />
          </label>
          <button
            type="submit"
            className="w-full rounded-lg bg-zinc-900 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-zinc-800"
          >
            Войти
          </button>
        </form>
      </div>
    </main>
  );
}

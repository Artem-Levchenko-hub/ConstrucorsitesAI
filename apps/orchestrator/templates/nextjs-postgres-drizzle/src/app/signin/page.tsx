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
import { AuthShell, AuthField, AuthSubmit } from "@/components/auth-shell";

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
    <AuthShell
      mode="signin"
      title="Вход"
      subtitle={
        <>
          Нет аккаунта?{" "}
          <Link
            href="/signup"
            className="font-medium text-[var(--brand)] hover:underline"
          >
            Зарегистрироваться
          </Link>
        </>
      }
    >
      {sp.error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Неверный email или пароль.
        </div>
      )}

      <form action={action} className="space-y-4">
        <input type="hidden" name="next" value={sp.next ?? "/"} />
        <AuthField
          label="Email"
          name="email"
          type="email"
          required
          autoComplete="email"
        />
        <AuthField
          label="Пароль"
          name="password"
          type="password"
          required
          minLength={8}
          autoComplete="current-password"
        />
        <AuthSubmit>Войти</AuthSubmit>
      </form>
    </AuthShell>
  );
}

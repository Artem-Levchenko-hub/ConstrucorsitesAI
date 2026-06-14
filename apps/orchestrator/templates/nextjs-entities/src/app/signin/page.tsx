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
import { APP_HOME } from "@/lib/session";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AuthShell } from "@/components/omnia";

export const metadata = { title: "Вход" };

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const sp = await searchParams;
  // Already signed in → bounce straight through to next= or home.
  const session = await auth();
  if (session?.user) redirect(sp.next ?? APP_HOME);

  async function action(formData: FormData) {
    "use server";
    const email = String(formData.get("email") ?? "").trim().toLowerCase();
    const password = String(formData.get("password") ?? "");
    const next = String(formData.get("next") ?? APP_HOME);
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
          <Link href="/signup" className="font-medium text-primary hover:underline">
            Зарегистрироваться
          </Link>
        </>
      }
    >
      {sp.error && (
        <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          Неверный email или пароль.
        </div>
      )}

      <form action={action} className="space-y-4">
        <input type="hidden" name="next" value={sp.next ?? APP_HOME} />
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
            autoComplete="current-password"
          />
        </div>
        <Button type="submit" size="lg" className="w-full">
          Войти
        </Button>
      </form>
    </AuthShell>
  );
}

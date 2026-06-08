/**
 * Pre-built sign-in page. AI should NOT regenerate this — the Auth.js
 * Credentials provider routes here on failed auth + this page calls
 * `signIn("credentials", ...)` from a server action. Restyling is fine
 * (Tailwind classes, copy, brand colors); core form structure isn't.
 */

import Link from "next/link";
import { redirect } from "next/navigation";
import { signIn, auth } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

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
    await signIn("credentials", { email, password, redirectTo: next });
  }

  return (
    <main className="grid min-h-screen place-items-center bg-muted/30 px-4 py-12">
      <div className="w-full max-w-sm rounded-2xl border border-border bg-card p-8 shadow-sm">
        <header className="mb-6 space-y-1 text-center">
          <h1 className="text-2xl font-semibold tracking-tight">Вход</h1>
          <p className="text-sm text-muted-foreground">
            Нет аккаунта?{" "}
            <Link href="/signup" className="font-medium text-primary hover:underline">
              Зарегистрироваться
            </Link>
          </p>
        </header>

        {sp.error && (
          <div className="mb-4 rounded-lg border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
            Неверный email или пароль.
          </div>
        )}

        <form action={action} className="space-y-4">
          <input type="hidden" name="next" value={sp.next ?? "/"} />
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
      </div>
    </main>
  );
}

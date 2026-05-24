import { GithubConnectionCard } from "@/components/account/GithubConnectionCard";
import { TopBar } from "@/components/workspace/TopBar";
import { getSession } from "@/lib/auth-mock";
import { redirect } from "next/navigation";

export default async function AccountPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  return (
    <>
      <TopBar user={session} showProjectControls={false} />
      <main className="mx-auto w-full max-w-2xl px-6 py-10 space-y-6">
        <header className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Аккаунт</h1>
          <p className="text-sm text-fg-tertiary">
            Внешние интеграции и доступы.
          </p>
        </header>

        <GithubConnectionCard />
      </main>
    </>
  );
}

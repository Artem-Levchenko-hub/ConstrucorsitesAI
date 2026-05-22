import { Suspense } from "react";
import { getSession } from "@/lib/auth-mock";
import { TopBar } from "@/components/workspace/TopBar";
import { GithubConnectionCard } from "@/components/account/GithubConnectionCard";

export default async function AccountPage() {
  const session = await getSession();
  if (!session) return null;

  return (
    <>
      <TopBar user={session} showProjectControls={false} />
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl px-6 py-10 space-y-8">
          <div className="space-y-1">
            <h1 className="text-3xl font-semibold tracking-tight">Аккаунт</h1>
            <p className="text-sm text-fg-secondary">
              Интеграции и подключённые сервисы.
            </p>
          </div>
          <Suspense>
            <GithubConnectionCard />
          </Suspense>
        </div>
      </div>
    </>
  );
}

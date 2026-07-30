import { AccountControlCenter } from "@/components/account/AccountControlCenter";
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
      <main className="studio-grid flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-4xl space-y-6 px-5 py-10 sm:px-8 sm:py-14">
        <header className="mb-10 space-y-2">
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-400">Настройки</p>
          <h1 className="text-3xl font-semibold tracking-[-0.035em]">Аккаунт и безопасность</h1>
          <p className="max-w-xl text-sm leading-6 text-fg-tertiary">
            Безопасность, реквизиты, платежи и управление данными.
          </p>
        </header>

        <GithubConnectionCard />
        <AccountControlCenter email={session.email} />
        </div>
      </main>
    </>
  );
}

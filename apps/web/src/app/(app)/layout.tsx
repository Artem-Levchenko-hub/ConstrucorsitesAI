import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth-mock";
import { headers } from "next/headers";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getSession();
  if (!session) {
    const target = (await headers()).get("x-omnia-return-to");
    const accountReturn = target && /^\/(account|billing)(?:[/?]|$)/.test(target);
    redirect(accountReturn ? `/login?next=${encodeURIComponent(target)}` : "/login");
  }

  // h-dvh (а не min-h-svh) — фиксируем высоту обёртки = viewport. Без этого
  // child-grid в Workspace растёт под content, h-full в ChatPanel перестаёт
  // каскадиться, инпут уезжает за нижний край viewport. overflow-hidden
  // дополнительно гарантирует что страничный скролл не появится — скроллятся
  // только внутренние блоки (chat history, code view, preview iframe).
  return (
    <div className="flex h-dvh max-h-dvh min-h-0 flex-col overflow-hidden">
      {children}
    </div>
  );
}

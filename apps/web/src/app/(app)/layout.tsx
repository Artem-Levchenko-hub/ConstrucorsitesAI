import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth-mock";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getSession();
  if (!session) redirect("/login");

  // h-dvh (а не min-h-svh) — фиксируем высоту обёртки = viewport. Без этого
  // child-grid в Workspace растёт под content, h-full в ChatPanel перестаёт
  // каскадиться, инпут уезжает за нижний край viewport. overflow-hidden
  // дополнительно гарантирует что страничный скролл не появится — скроллятся
  // только внутренние блоки (chat history, code view, preview iframe).
  return (
    <div className="h-dvh flex flex-col overflow-hidden">{children}</div>
  );
}

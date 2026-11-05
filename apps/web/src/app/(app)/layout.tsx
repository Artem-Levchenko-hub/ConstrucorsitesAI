import { redirect } from "next/navigation";
import { getSession } from "@/lib/auth-mock";
import { headers } from "next/headers";
import { isMaxOwnerPreviewPath } from "@/lib/max-owner-preview-path";
import { maxStaffReturnPath } from "@/lib/max-staff-path";
import { Suspense } from "react";
import { ProductLearning } from "@/components/learning/ProductLearning";
import { LearningProvider } from "@/components/learning/LearningProvider";

export default async function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getSession();
  if (!session) {
    const target = (await headers()).get("x-omnia-return-to");
    const accountReturn = target && /^\/(account|billing)(?:[/?]|$)/.test(target);
    const ownerPreviewReturn = target && isMaxOwnerPreviewPath(target);
    const returnPath = maxStaffReturnPath(target) ?? (accountReturn || ownerPreviewReturn ? target : null);
    redirect(returnPath ? `/login?next=${encodeURIComponent(returnPath)}` : "/login");
  }
  const target = (await headers()).get("x-omnia-return-to");
  if (session.registrationVerificationReason && target && /^\/billing(?:[/?]|$)/.test(target)) {
    redirect(`/max/onboarding?next=${encodeURIComponent(target)}`);
  }

  // h-dvh (а не min-h-svh) — фиксируем высоту обёртки = viewport. Без этого
  // child-grid в Workspace растёт под content, h-full в ChatPanel перестаёт
  // каскадиться, инпут уезжает за нижний край viewport. overflow-hidden
  // дополнительно гарантирует что страничный скролл не появится — скроллятся
  // только внутренние блоки (chat history, code view, preview iframe).
  return (
    <LearningProvider key={session.id} userKey={session.id}>
      <div className="flex h-dvh max-h-dvh min-h-0 flex-col overflow-hidden">
        {children}
        {!session.registrationVerificationReason && <Suspense fallback={null}><ProductLearning userKey={session.id} /></Suspense>}
      </div>
    </LearningProvider>
  );
}

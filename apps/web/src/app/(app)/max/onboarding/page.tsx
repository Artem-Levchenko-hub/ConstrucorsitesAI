import { redirect } from "next/navigation";

import { MaxOnboarding } from "@/components/max/MaxOnboarding";
import { MaxStudioHeader } from "@/components/max/MaxStudioHeader";
import { getSession } from "@/lib/auth-mock";
import { safeReturnPath } from "@/lib/auth-return";

export default async function MaxOnboardingPage({ searchParams }: { searchParams?: Promise<{ next?: string }> }) {
  const session = await getSession();
  if (!session || session.isAnon) redirect("/max/register");
  const next = safeReturnPath((await searchParams)?.next) ?? "/max";

  return (
    <div data-product-shell className="flex h-full min-h-0 flex-col overflow-hidden bg-[#121519]">
      <MaxStudioHeader email={session.email} />
      <MaxOnboarding email={session.email} next={next} />
    </div>
  );
}

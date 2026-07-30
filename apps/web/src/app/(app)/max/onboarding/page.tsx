import { redirect } from "next/navigation";

import { MaxOnboarding } from "@/components/max/MaxOnboarding";
import { MaxStudioHeader } from "@/components/max/MaxStudioHeader";
import { getSession } from "@/lib/auth-mock";

export default async function MaxOnboardingPage() {
  const session = await getSession();
  if (!session || session.isAnon) redirect("/max/register");

  return (
    <div data-light-shell className="flex h-full min-h-0 flex-col overflow-hidden bg-[#f5f3ee]">
      <MaxStudioHeader email={session.email} />
      <MaxOnboarding email={session.email} />
    </div>
  );
}

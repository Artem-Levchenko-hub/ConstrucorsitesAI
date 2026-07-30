import { redirect } from "next/navigation";

import { MaxOnboarding } from "@/components/max/MaxOnboarding";
import { MaxStudioHeader } from "@/components/max/MaxStudioHeader";
import { getSession } from "@/lib/auth-mock";

export default async function MaxOnboardingPage() {
  const session = await getSession();
  if (!session || session.isAnon) redirect("/max/register");

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden bg-[#0b0c12]">
      <MaxStudioHeader email={session.email} />
      <MaxOnboarding email={session.email} />
    </div>
  );
}

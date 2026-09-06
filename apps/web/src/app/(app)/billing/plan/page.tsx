import { AccountControlCenter } from "@/components/account/AccountControlCenter";
import { AccountShell } from "@/components/account/AccountShell";
import { getSession } from "@/lib/auth-mock";
import { redirect } from "next/navigation";

export default async function PlanPage({ searchParams }: { searchParams: Promise<{ payment?: string }> }) {
  const session = await getSession();
  if (!session) {
    const { payment } = await searchParams;
    const target = payment ? `/billing/plan?payment=${encodeURIComponent(payment)}` : "/billing/plan";
    redirect(`/login?next=${encodeURIComponent(target)}`);
  }
  return (
    <AccountShell email={session.email} active="plan">
      <AccountControlCenter email={session.email} view="plan" />
    </AccountShell>
  );
}

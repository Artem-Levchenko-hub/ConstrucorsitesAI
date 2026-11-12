import { AccountControlCenter } from "@/components/account/AccountControlCenter";
import { AccountShell } from "@/components/account/AccountShell";
import { getSession } from "@/lib/auth-mock";
import { redirect } from "next/navigation";

export default async function PlanPage({ searchParams }: { searchParams: Promise<{ payment?: string; plan?: string }> }) {
  const session = await getSession();
  if (!session) {
    const { payment, plan } = await searchParams;
    const query = new URLSearchParams();
    if (typeof payment === "string" && payment) query.set("payment", payment);
    if (typeof plan === "string" && plan) query.set("plan", plan);
    const target = query.size ? `/billing/plan?${query}` : "/billing/plan";
    redirect(`/login?next=${encodeURIComponent(target)}`);
  }
  return (
    <AccountShell email={session.email} active="plan">
      <AccountControlCenter email={session.email} view="plan" />
    </AccountShell>
  );
}

import { AccountControlCenter } from "@/components/account/AccountControlCenter";
import { AccountShell } from "@/components/account/AccountShell";
import { getSession } from "@/lib/auth-mock";
import { redirect } from "next/navigation";

export default async function BillingPage() {
  const session = await getSession();
  if (!session) redirect("/login");
  return <AccountShell email={session.email} active="billing"><AccountControlCenter email={session.email} view="billing" /></AccountShell>;
}

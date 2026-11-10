import type { Metadata } from "next";
import { MaxStaffInvite } from "@/components/max/MaxStaffInvite";
import { getSession } from "@/lib/auth-mock";
import { serverApiFetch } from "@/lib/api/server";

export const metadata: Metadata = { title: "Приглашение в команду — Yleum", robots: { index: false, follow: false }, referrer: "no-referrer" };

export default async function MaxStaffJoinPage() {
  const session = await getSession();
  const authenticated = Boolean(session && !session.isAnon);
  const verified = Boolean(session?.emailVerifiedAt);
  const access = authenticated ? await serverApiFetch<{ email_delivery_configured: boolean }>("/api/max/account/access") : null;
  return <MaxStaffInvite authenticated={authenticated} verified={verified} email={authenticated ? session?.email ?? null : null}
    emailDeliveryAvailable={Boolean(access?.email_delivery_configured)} />;
}

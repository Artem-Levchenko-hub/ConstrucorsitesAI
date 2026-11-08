import { getSession } from "@/lib/auth-mock";
import { getPublicBillingPlans } from "@/lib/public-billing-plans";

import { YleumLanding } from "./YleumLanding";

export async function YleumLandingPage() {
  const [plans, session] = await Promise.all([getPublicBillingPlans(), getSession()]);
  return <YleumLanding plans={plans} signedIn={!!session && !session.isAnon} />;
}

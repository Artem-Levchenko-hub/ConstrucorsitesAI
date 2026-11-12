import { cache } from "react";

import { getSession } from "@/lib/auth-mock";
import { getPublicBillingPlans } from "@/lib/public-billing-plans";

import { YleumLanding } from "./YleumLanding";

const getRequestTime = cache(Date.now);

export async function YleumLandingPage() {
  const [plans, session] = await Promise.all([getPublicBillingPlans(), getSession()]);
  return <YleumLanding plans={plans} signedIn={!!session && !session.isAnon} now={getRequestTime()} />;
}

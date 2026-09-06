"use client";
import { AccountProfile } from "./AccountProfile";
import { AccountOrganization } from "./AccountOrganization";
import { AccountSecurity } from "./AccountSecurity";
import { AccountBilling } from "./AccountBilling";
import { AccountPlan } from "./AccountPlan";
import { AccountTransactions } from "./AccountTransactions";
import { PaymentCheckout, usePaymentJourney } from "./PaymentCheckout";
import "@/components/max/max-studio.css";
import "./account.css";

export type AccountView = "all" | "profile" | "organization" | "security" | "billing" | "transactions" | "plan" | "admin";
export function AccountControlCenter({ email, view = "all" }: { email: string; view?: AccountView }) {
  const journey = usePaymentJourney(email);
  return <div className="account-content">
    <PaymentCheckout journey={journey} />
    {(view === "profile" || view === "all") && <AccountProfile email={email} />}
    {(view === "organization" || view === "all") && <AccountOrganization />}
    {(view === "security" || view === "all") && <AccountSecurity />}
    {(view === "billing" || view === "all") && <AccountBilling journey={journey} />}
    {(view === "transactions" || view === "all") && <AccountTransactions />}
    {(view === "plan" || view === "all") && <AccountPlan journey={journey} />}
  </div>;
}

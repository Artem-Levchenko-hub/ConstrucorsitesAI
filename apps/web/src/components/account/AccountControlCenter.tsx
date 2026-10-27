"use client";
import { AccountProfile } from "./AccountProfile";
import { AccountSecurity } from "./AccountSecurity";
import { AccountBilling } from "./AccountBilling";
import { AccountPlan } from "./AccountPlan";
import { AccountTransactions } from "./AccountTransactions";
import { PaymentCheckout, usePaymentJourney } from "./PaymentCheckout";
import "@/components/max/max-studio.css";
import "./account.css";

export type AccountView = "all" | "profile" | "security" | "billing" | "transactions" | "plan" | "admin";
type AccountControlCenterProps = { email: string; view?: AccountView };

export function AccountControlCenter(props: AccountControlCenterProps) {
  return <AccountControlCenterSession key={props.email} {...props} />;
}

function AccountControlCenterSession({ email, view = "all" }: AccountControlCenterProps) {
  const journey = usePaymentJourney(email);
  return <div className="account-content">
    <PaymentCheckout journey={journey} />
    {(view === "profile" || view === "all") && <AccountProfile key={email} email={email} />}
    {(view === "security" || view === "all") && <AccountSecurity key={email} email={email} />}
    {(view === "billing" || view === "all") && <AccountBilling journey={journey} />}
    {(view === "transactions" || view === "all") && <AccountTransactions />}
    {(view === "plan" || view === "all") && <AccountPlan journey={journey} />}
  </div>;
}

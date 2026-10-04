-- Additive runtime rights for generation billing reconciliation after 0075.
-- Run as the existing DB administrator with psql -v billing_role=max_billing.
-- This helper never creates roles, changes credentials/HBA, or rewrites data.
-- No broad/default grants: immutable receipts/policies remain read-only.
BEGIN;
GRANT SELECT, UPDATE ON TABLE public.generation_runs TO :"billing_role";
GRANT SELECT ON TABLE
  public.generation_billing_policies, public.usage, public.messages, public.snapshots
  TO :"billing_role";
GRANT SELECT, INSERT ON TABLE public.generation_billing_intents TO :"billing_role";
GRANT SELECT, INSERT, UPDATE ON TABLE public.generation_billing_outbox TO :"billing_role";
COMMIT;

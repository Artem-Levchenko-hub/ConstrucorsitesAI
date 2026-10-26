-- Run after API migrations as DB administrator with psql -v billing_role=max_billing.
-- Repeatable bootstrap/runtime grants; the equivalent historical 0094 DDL is frozen.
-- The worker may inspect proof metadata, without authentication secrets or identity writes.
BEGIN;
GRANT SELECT, INSERT, UPDATE ON TABLE public.email_notifications TO :"billing_role";
GRANT SELECT (id, user_id, purpose, used_at, expires_at)
  ON TABLE public.auth_tokens TO :"billing_role";
GRANT SELECT (id, user_id, provider, provider_user_id, email)
  ON TABLE public.user_identities TO :"billing_role";
COMMIT;

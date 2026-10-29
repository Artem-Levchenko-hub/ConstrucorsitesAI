-- Portable products do not have max_users. Preserve the existing compatible
-- max_business_actions and its FK in legacy apps; never create a profile table.
CREATE TABLE IF NOT EXISTS max_business_actions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), max_user_id text NOT NULL,
 action_type text NOT NULL, status text NOT NULL DEFAULT 'new',
 payload jsonb NOT NULL DEFAULT '{}'::jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS max_owner_records (
 record_id uuid PRIMARY KEY REFERENCES max_business_actions(id) ON DELETE RESTRICT,
 project_id uuid NOT NULL, max_user_id text NOT NULL,
 resource text NOT NULL CHECK(resource IN ('orders','leads','bookings')),
 status text NOT NULL DEFAULT 'new' CHECK(status IN ('new','in_progress','completed','cancelled')),
 note text NOT NULL DEFAULT '' CHECK(char_length(note)<=2000), revision text NOT NULL CHECK(revision ~ '^[a-f0-9]{64}$')
);
CREATE TABLE IF NOT EXISTS max_business_receipts (
 max_user_id text NOT NULL, operation_key uuid NOT NULL, digest text NOT NULL,
 response jsonb NOT NULL, PRIMARY KEY(max_user_id,operation_key)
);
CREATE TABLE IF NOT EXISTS max_owner_receipts (
 project_id uuid NOT NULL, owner_id uuid NOT NULL, operation_key uuid NOT NULL,
 digest text NOT NULL, response jsonb NOT NULL, PRIMARY KEY(project_id,owner_id,operation_key)
);
CREATE TABLE IF NOT EXISTS max_owner_audit (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), project_id uuid NOT NULL, owner_id uuid NOT NULL,
 record_id uuid NOT NULL REFERENCES max_owner_records(record_id), operation_key uuid NOT NULL,
 old_status text NOT NULL, new_status text NOT NULL, old_note text NOT NULL, new_note text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(project_id,owner_id,operation_key)
);
CREATE INDEX IF NOT EXISTS max_owner_records_resource_idx ON max_owner_records(resource,record_id);
CREATE INDEX IF NOT EXISTS max_business_actions_managed_page_idx ON max_business_actions(created_at DESC,id DESC);
ALTER TABLE max_business_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE max_business_actions FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS max_business_actions_own_rows ON max_business_actions;
CREATE POLICY max_business_actions_own_rows ON max_business_actions
 USING (max_user_id=nullif(current_setting('app.max_user_id',true),''))
 WITH CHECK (max_user_id=nullif(current_setting('app.max_user_id',true),''));
ALTER TABLE max_owner_records ENABLE ROW LEVEL SECURITY;
ALTER TABLE max_owner_records FORCE ROW LEVEL SECURITY;
CREATE POLICY max_owner_records_customer_read ON max_owner_records FOR SELECT
 USING (max_user_id=nullif(current_setting('app.max_user_id',true),''));
CREATE POLICY max_owner_records_customer_insert ON max_owner_records FOR INSERT
 WITH CHECK (max_user_id=nullif(current_setting('app.max_user_id',true),'')
 AND project_id::text=nullif(current_setting('app.management_project_id',true),'')
 AND status='new' AND note='' AND EXISTS(SELECT 1 FROM max_business_actions a
 WHERE a.id=record_id AND a.max_user_id=max_owner_records.max_user_id
 AND a.action_type=CASE resource WHEN 'orders' THEN 'order_created' WHEN 'leads' THEN 'lead_created' WHEN 'bookings' THEN 'booking_created' END
 AND a.payload->>'management_protocol'='1' AND a.payload->>'resource'=resource));
CREATE POLICY max_owner_records_owner_read ON max_owner_records FOR SELECT
 USING (project_id::text=nullif(current_setting('app.owner_project_id',true),'') AND nullif(current_setting('app.owner_id',true),'') IS NOT NULL);
CREATE POLICY max_owner_records_owner_update ON max_owner_records FOR UPDATE
 USING (project_id::text=nullif(current_setting('app.owner_project_id',true),'') AND nullif(current_setting('app.owner_id',true),'') IS NOT NULL)
 WITH CHECK (project_id::text=nullif(current_setting('app.owner_project_id',true),'') AND nullif(current_setting('app.owner_id',true),'') IS NOT NULL);
CREATE POLICY max_business_actions_owner_read ON max_business_actions FOR SELECT
 USING (action_type IN ('order_created','lead_created','booking_created')
 AND EXISTS(SELECT 1 FROM max_owner_records m WHERE m.record_id=id
 AND m.project_id::text=nullif(current_setting('app.owner_project_id',true),'')
 AND aCTION_type=CASE m.resource WHEN 'orders' THEN 'order_created' WHEN 'leads' THEN 'lead_created' WHEN 'bookings' THEN 'booking_created' END)
 AND nullif(current_setting('app.owner_id',true),'') IS NOT NULL);
ALTER TABLE max_business_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE max_business_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY max_business_receipts_actor ON max_business_receipts
 USING(max_user_id=nullif(current_setting('app.max_user_id',true),''))
 WITH CHECK(max_user_id=nullif(current_setting('app.max_user_id',true),''));
ALTER TABLE max_owner_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE max_owner_receipts FORCE ROW LEVEL SECURITY;
CREATE POLICY max_owner_receipts_identity ON max_owner_receipts
 USING(project_id::text=nullif(current_setting('app.owner_project_id',true),'') AND owner_id::text=nullif(current_setting('app.owner_id',true),''))
 WITH CHECK(project_id::text=nullif(current_setting('app.owner_project_id',true),'') AND owner_id::text=nullif(current_setting('app.owner_id',true),''));
ALTER TABLE max_owner_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE max_owner_audit FORCE ROW LEVEL SECURITY;
CREATE POLICY max_owner_audit_identity ON max_owner_audit
 USING(project_id::text=nullif(current_setting('app.owner_project_id',true),'') AND owner_id::text=nullif(current_setting('app.owner_id',true),''))
 WITH CHECK(project_id::text=nullif(current_setting('app.owner_project_id',true),'') AND owner_id::text=nullif(current_setting('app.owner_id',true),''));
-- Registered customer snapshots cannot be changed by old generic APIs either.
CREATE FUNCTION max_guard_managed_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM max_owner_records WHERE record_id=OLD.id) THEN
  RAISE EXCEPTION 'managed snapshot is immutable';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER max_managed_snapshot_immutable BEFORE UPDATE OR DELETE ON max_business_actions FOR EACH ROW EXECUTE FUNCTION max_guard_managed_snapshot();

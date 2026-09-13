-- Additive managed storage. Existing application data is never rewritten here.
CREATE TABLE IF NOT EXISTS omnia_secure_records (
  project_id text NOT NULL,
  collection text NOT NULL,
  id uuid NOT NULL,
  owner_id text NOT NULL,
  ciphertext text NOT NULL,
  revision integer NOT NULL CHECK (revision > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (project_id, collection, id)
);
CREATE INDEX IF NOT EXISTS omnia_secure_records_owner_page
  ON omnia_secure_records (project_id, collection, owner_id, id);
CREATE TABLE IF NOT EXISTS omnia_secure_record_audit (
  id bigserial PRIMARY KEY,
  project_id text NOT NULL,
  collection text NOT NULL,
  record_id uuid NOT NULL,
  actor_id text NOT NULL,
  action text NOT NULL CHECK (action IN ('create', 'update', 'delete')),
  revision integer NOT NULL CHECK (revision > 0),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS omnia_secure_record_audit_record
  ON omnia_secure_record_audit (project_id, collection, record_id, created_at);

-- 0001: clients
CREATE TABLE IF NOT EXISTS clients (
  id serial PRIMARY KEY,
  name text NOT NULL,
  phone text,
  status text NOT NULL DEFAULT 'new',
  created_at timestamp NOT NULL DEFAULT now()
);

ALTER TABLE clients
  ADD CONSTRAINT clients_status_check
  CHECK (status IN ('new', 'vip'));

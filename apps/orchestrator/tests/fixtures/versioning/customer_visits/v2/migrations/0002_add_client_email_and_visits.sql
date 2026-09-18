-- 0002: required email (technical surrogate backfill) + visits
ALTER TABLE clients ADD COLUMN email text;
UPDATE clients SET email = 'synthetic+' || regexp_replace(phone, '\D', '', 'g') || '@example.invalid';
ALTER TABLE clients ALTER COLUMN email SET NOT NULL;

CREATE TABLE visits (
  id serial PRIMARY KEY,
  client_id integer NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  amount numeric(12, 2) NOT NULL CHECK (amount >= 0),
  visited_at timestamp NOT NULL DEFAULT now()
);

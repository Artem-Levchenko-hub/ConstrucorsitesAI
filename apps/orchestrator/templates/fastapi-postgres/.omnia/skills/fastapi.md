# Skill: fastapi — production-grade Python API by construction

Read before any endpoint, model, query, or migration. Build it layered, typed,
and injection-free the first time — the SAST gate scans the `.py` you write.

## Layering (don't put SQL in the route)
- **routes → services → repositories → DB.** The route parses/validates input
  and returns a response model; the service holds business logic; the repository
  is the ONLY layer that runs queries. A handler that builds SQL inline is the
  thing to avoid.
- Use the project's session/engine + dependency injection (`Depends`) — don't open
  ad-hoc connections.

## Validation at the edge (Pydantic)
- Every request body / query param is a Pydantic model with real types and
  constraints (`Field(gt=0)`, `EmailStr`, `max_length`). Never accept a raw dict.
- Every response uses a `response_model` — never leak the ORM row (hashed
  password, internal flags) straight out.

## No injection (the SAST gate blocks these)
- **SQL:** parameterized ONLY — SQLAlchemy expression API or bound params. NEVER
  build SQL with an f-string / `%` / `.format` / concatenation
  (`execute(f"... {x}")` is rejected).
- **Commands:** no `os.system`, no `subprocess(..., shell=True)`. If you must
  shell out, pass an args list with `shell=False`.
- **Code:** never `eval`/`exec` on input.

## Secrets + auth
- Read secrets from env (`os.environ` / pydantic `BaseSettings`), never hard-code a
  key/password/token. Need a new key → name the env var and stop.
- Authenticate every protected route (the project's auth dependency); authorize by
  role/ownership in the service layer; never trust a client-supplied user id.
- Hash passwords with the project's hasher (Argon2/bcrypt) — never store or log
  plaintext.

## Migrations (Alembic — never hand-edit the DB)
- Every schema change = an Alembic revision (`alembic revision --autogenerate`),
  reviewed, with a real `downgrade()`. Don't `create_all` in app code for schema
  evolution; don't edit tables by hand. Migrations are versioned + reversible.

## Reliability (Release It!)
- Set timeouts on outbound calls; return typed errors (`HTTPException` with the
  right status), never a bare 500; validate at the boundary so bad input fails
  fast with 422.

Self-check before `done`: no SQL string-built; every body/response is a Pydantic
model; secrets from env; protected routes authed; schema change has an Alembic
revision with a downgrade.

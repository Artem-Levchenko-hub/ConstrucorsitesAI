# Security audit corrections — approved scope

The owner requested all five corrections from the 2026-10-30 Yleum security audit,
in this order: restore complete Project Cell backup, then authentication and long
password handling. Preserve customer data and prove working behavior. The audit
and its concrete recommendations are the approved requirements for this work.

## Decisions

- Preserve strict backup inventory validation. Its live failure is five exited,
  consent-QA-only containers whose `/tmp` bind sources have already disappeared.
  Preserve their complete filesystem snapshots, inspection metadata and logs,
  verify that archive, then remove only those exact still-exited identities.
  Preserve all volumes, active containers and old backup copies.
- Run the canonical full backup and its scratch database restore verifier before
  application changes. An encrypted-envelope check is not a ciphertext recovery;
  keep the offline-key limitation explicit unless independent recovery succeeds.
- Use a shared exact trusted-Origin policy for unsafe cookie-authenticated HTTP
  and project WebSocket handshakes. Preserve cookie-free service/Bearer clients,
  ownership checks and session revocation. Update server-side web forwarding.
- Bind each OAuth state and pending-registration ticket to its initiating browser
  with short-lived per-flow host-only cookies. No database migration, parent-domain
  binding cookie, reused global cookie, or consumed foreign-browser state.
- New password writes use Passlib bcrypt_sha256 v2 with existing bcrypt work factor.
  Keep bcrypt readers. Ship dual readers before enabling new writes, so a compatible
  rollback revision exists. Never infer or silently enroll a lost legacy suffix.
  The owner explicitly chose preserving old login, including legacy >=72-byte
  behavior, until explicit password change/reset. Do not silently migrate an
  ambiguous legacy suffix or force recovery. Document that accepted limitation;
  retain accounts, business data and unrelated current sessions.

## Acceptance

Full backup has complete three-node coverage and successful isolated restore;
foreign/missing/null origins have no mutation or socket acceptance; legitimate
web and service flows still work; foreign-browser OAuth callbacks/tickets cannot
sign in, disclose pending identity or consume a valid flow; concurrent flows work;
new passwords remain sensitive to all accepted UTF-8 bytes. Existing short legacy
passwords still authenticate. Tests, independent review, exact CI, canonical
production rollout and live health evidence are mandatory for delivered code.

No paid generations, broad cleanup, customer-data restore targets, credential
rotation, schema reset or unrelated infrastructure replacement are authorized.
# Backup investigation addendum, 2026-10-30

After preserving and reconciling the five exited consent-QA containers, the
full collector reached Runtime and rejected the legitimate analytics relay.
The repair models this component explicitly, binds its source and all replicas,
requires the exact guard image, and preserves unknown-component rejection and
exclusive private file creation. Old guard-only artifacts remain readable.

The owner approved a new offline recipient for future copies after the current
recipient had no matching available private key. Keep all old keys and archives;
never upload the new private key. Verify the new encrypted copy by decryption
and isolated restore before application-auth deployment.

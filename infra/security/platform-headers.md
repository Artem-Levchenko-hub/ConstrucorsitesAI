# Platform HTTPS response policy

The canonical platform vhost is `/etc/nginx/sites-available/yleum.ru`. The
tracked generator is `infra/max-k3s/migrate/30-bring-up.sh`. Do not rerun that
migration to update response headers: it also restores databases and resets
runtime state.

The two nginx files cover HTTPS transport and MIME interpretation only. Their
variables are empty on HTTP, and only the platform vhost includes the header
snippet. Include it both at server scope and in `/otchet/` and `/minio/`, whose
own `add_header Cache-Control` directives disable parent `add_header`
inheritance. The snippet replaces upstream HSTS/nosniff values so the edge
emits one authoritative value. It deliberately contains no framing policy.

`apps/web/next.config.ts` owns platform web framing: `SAMEORIGIN` and
`frame-ancestors 'self'; object-src 'none'; base-uri 'self'`. This does not
restrict Studio's child preview frames or Next inline scripts. It is a minimal
framing policy, not a complete script/XSS policy. Generated owner/public MAX
boundaries and `/api/integrations/moysklad/setup` keep their own CSP and allowed
parents; do not install platform framing headers in the nginx `http` context,
the whole platform vhost, or generated app/preview vhosts.

## Controlled canonical apply proposal

Root executes this after the intended revision has passed CI and is delivered
through the canonical release path, with the usual generation/publication
fences. Use these files from that exact revision; verify their hashes against
the release evidence. Keep the evidence directory private (`0700`). This is an
operator proposal, not an automatically executed release hook.

1. Record the canonical vhost SHA256 and metadata. Back up that vhost and any
   existing `/etc/nginx/conf.d/yleum-platform-header-values.conf` and
   `/etc/nginx/snippets/yleum-platform-https-headers.conf`, recording absence
   explicitly for new files. Preserve ownership and mode in the backups.
2. Prepare an exclusive candidate. The helper reads only the fixed canonical
   vhost, verifies its supplied hash, accepts only the reviewed two-server and
   two-cache-header shape, and writes a `0600` candidate without installing it:

   ```sh
   sudo python3 /opt/omnia/infra/security/prepare-platform-headers.py \
     --prepare --expected-sha256 "$platform_vhost_before_sha" \
     --output "$platform_header_record/yleum.ru.candidate"
   ```

   Review the four added include lines; every other vhost byte must match the
   original. A changed hash, additional header override, foreign host, or
   partial/misplaced prior installation stops preparation for review.
3. Immediately recheck the original vhost SHA256 (including any release drain
   changes). Install the exact-revision policy files:

   ```sh
   sudo install -d -m 755 /etc/nginx/snippets
   sudo install -m 644 /opt/omnia/infra/security/nginx/platform-header-values.conf \
     /etc/nginx/conf.d/yleum-platform-header-values.conf
   sudo install -m 644 /opt/omnia/infra/security/nginx/platform-https-headers.conf \
     /etc/nginx/snippets/yleum-platform-https-headers.conf
   ```

   Install the reviewed vhost candidate while preserving the original vhost
   owner and mode. Run `sudo nginx -t` before `sudo systemctl reload nginx`.
   If validation or reload fails, restore all three backed-up paths, remove
   only paths recorded as newly created by this update, validate again, and
   restore the previous running configuration. Record failures explicitly.
4. Verify actual HTTPS responses on both apex and www: document, protected
   redirect, cache-override static locations, and edge error statuses. HSTS is
   exactly `max-age=31536000` without subdomain/preload directives; nosniff is
   present once. Platform web document/RSC/action responses keep their framing
   headers and existing login/logout no-store/cache-only Clear-Site-Data.
   Check that platform HTTP still redirects and emits no HSTS. Retain separate
   embedding checks for owner preview cabinet origins, public MAX origins,
   and MoySklad's allowed parent; duplicate CSP policies must not block them.

## Local checks

Run `python3 infra/security/test-platform-headers.py`, web tests/typecheck/lint,
and `bash -n infra/max-k3s/migrate/30-bring-up.sh`. Existing embedded-surface
regressions live in `apps/orchestrator/tests/test_nginx_writer.py`,
`apps/orchestrator/tests/test_machine_boundary.py`, and
`apps/api/tests/test_security_gate.py`. Actual nginx configuration and response
verification is required before claiming production header acceptance.

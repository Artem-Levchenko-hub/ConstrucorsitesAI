# Public MinIO media boundary

The public `/minio/` proxy serves approved media reads only. Customer source
archives, repository and backup objects must never be accessible through it,
even with a valid old query signature or an authenticated signing identity.
API/worker S3 clients continue using the internal endpoint and header auth.

Removing anonymous bucket Allow entries does not revoke authenticated
presigned URLs. MinIO's authenticated authorization path uses IAM policies;
the root owner bypasses those policies. The supported condition key is
`s3:authType`, whose query-auth value is `REST-QUERY-STRING`; `aws:authType` is
not that key. A bucket-policy conditional Deny is therefore not sufficient
for this requirement. No claim is made that any old signed export existed.

`public_media_read_boundary_map()` in `yleum_api.core.minio` generates an nginx
`http`-scope map directly from `_public_media_keys()`, including validated
literal legacy image exceptions and configured public bucket aliases. It
does not read MinIO objects or credentials, create a client, or change policy.
Remaining percent escapes, private buckets and non-media paths are denied.
The map uses normalized `$uri`, matching the normalized URI used by the
existing URI-bearing `proxy_pass`; signatures and other query parameters
cannot turn a denied path into an allowed one. The map preserves current
media writer paths, including MP4 videos. The vhost candidate adds a GET/HEAD
method guard and changes the one public location to `^~ /minio/` so unrelated
regex locations cannot supersede it. Other vhost bytes and the upstream stay
unchanged. This is a public boundary, not a restriction on internal S3/DR.

## Root-only controlled cutover

Do not rerun `infra/max-k3s/migrate/30-bring-up.sh`: it changes databases and
runtime state. Its historical nginx generator predates this controlled
boundary. Any later vhost regeneration must prepare and reapply the boundary
before that vhost is activated. There is no automatic production hook here.

1. Deliver the reviewed successor through the canonical release path. Pin
   API revision, current public-media settings/policy hashes, the canonical
   `/etc/nginx/sites-available/yleum.ru` SHA256 and all public MinIO ingress
   hosts/paths/ports. Confirm host S3 remains loopback-bound and there is no
   alternate public route around this boundary. Inspect the actual MinIO
   version: the compose tag `latest` is not an installed-version attestation.
2. From that exact API process environment, render
   `yleum_api.core.minio.public_media_read_boundary_map()` into a new private
   `0600` artifact. Importing this function and rendering it performs no
   object-store requests. Do not export the environment, access keys, object
   inventory or raw legacy configuration. Record the artifact SHA256.
   The map must correspond to the same current settings as the bucket policy;
   if approved keys or bucket aliases change, regenerate both before service.
3. Run `prepare-minio-public-boundary.py` with `--prepare`, the exact current
   `--expected-sha256` and a new private `--output`. Without `--prepare`, the
   helper reads nothing and reports zero mutations. It never installs files
   or reloads nginx. It parses balanced server/location ownership, including
   quoted tokens and Certbot listeners after locations. It requires exactly
   one TLS server, with the sole MinIO proxy inside that actual server's
   `/minio/` location. Other proxies must match the reviewed route-to-numeric
   loopback mapping for web/API/gateway; alternate `/s3/` routes, DNS/network
   aliases, variables, named locations and upstream blocks are refused.
   Inherited `error_page`, `rewrite`, dynamic routing and unknown directives
   anywhere in this vhost are refused. Known header/Certbot includes require
   their actual contents to contain only passive header/TLS directives; the
   CLI reads those exact non-symlink files and records hashes without printing
   contents. Missing, modified executable, transitive or custom includes stop
   preparation. Partial guards or changed source hashes also stop it.
4. Review the candidate and the generated map. Back up canonical vhost and
   `/etc/nginx/conf.d/yleum-public-media-read-boundary.conf`, preserving modes
   and recording absence. Recheck original source and map hashes immediately
   before installation. Install the map at that `conf.d` path and install
   only the reviewed vhost candidate. Run `nginx -t` before reload. If config
   validation or reload fails, restore both backed-up paths (remove only a
   file recorded as newly created), validate and restore the prior config.
   The candidate receipt explicitly covers `vhost_and_passive_includes_only`.
   Inspect a private hash-pinned full `nginx -T` capture before cutover: ambient
   `http`-scope configuration is not inside this vhost and must not supply
   inherited `error_page`, rewrite, dynamic routing or an alternate public
   S3 route. Check every relevant effective server/include and origin port,
   not only this candidate file. Recheck all recorded passive include hashes
   immediately before installing/reloading; a change invalidates preparation.
5. Verify apex/www and every old public MinIO hostname: synthetic nonexistent
   archive/private paths return edge 403 with and without SigV4-shaped query
   parameters, including normalized traversal/encoding cases. This proves
   path denial, not existence or validity of a historical signed export.
   If a restricted actual old URL is available, test it without exposing its
   signature. Verify real approved images/previews/legacy exceptions/videos,
   HEAD and Range reads, plus authenticated internal publication/repo/DR.
   Check alternate ports, aliases and direct origin access separately.

Offline pattern regressions are not an nginx engine or live acceptance test.
No nginx binary was available in the preparation environment. Until actual
config validation and ingress acceptance complete, this successor is prepared
and tested only; it is not a deployed closure of the old-link requirement.

The original v1 preparation helper was rejected in independent review: it
accepted alternate S3 proxy routes/named error redirects and inferred TLS
ownership from positions rather than balanced server blocks. Its frozen
artifacts remain historical evidence; use only the reviewed successor.

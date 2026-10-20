# Production database backup contract

The canonical source is `/opt/omnia/infra/backup`, from the delivered Git revision.
Do not run a frozen `/root/yleum-bootstrap-*/backup` copy. The platform entrypoint
and the controller-user wrapper are installed separately from application rollout.

The explicit topology requires Core, Commerce and runtime. Core/Commerce collect
their local Docker cell databases and state journals. Runtime lists every `app-*`
namespace and logically dumps both `core-postgres` and `project-postgres`, using
the live pod's pinned PostgreSQL image and a read-only administrative session.
Namespace/pod/container/PVC identities must remain equal after the dumps. Retired
namespaces can be excluded only when both pod and PVC inventories are empty.
Legacy retired namespaces may lack all three ownership labels; partial or
contradictory ownership labels remain invalid. Active namespaces require all
ownership labels. Publication `locks`, `identities` and the private 32-byte
`fingerprint.key` are validated service artifacts, archived rather than read as
project journals. Other publication entries must be strict UUID journal paths.
Commerce/Core published journals must match the runtime project set exactly.
The platform census contains all current project and workspace identities, not a
backup-start filter. Its UTC `Z` observation must be at most 3600 seconds old and
at most 60 seconds in the future when collection begins. The same validated JSON
is forwarded to the peer controller; Docker observations are collected locally
on each host. Offline verification evaluates the captured census relative to
`coverage.started_at`, so a valid older backup does not expire while archived.
`retired_no_local_storage` records verified storage absence rather than claiming
that a historical database was successfully dumped.

Retired namespaces with retained PVCs are separate `cold_retained` obligations,
not exclusions or active logical databases. The collector holds the **existing**
canonical Core publication lock inode and binds its disabled/deletion-completed
journal to the Core state archive. It neither creates lock files nor changes
their owners. An unknown/missing retirement authority blocks collection.

One pinned helper Pod mounts every retained PVC read-only, with no fsGroup,
chown/init container, service-account token, host namespace or secret mount.
A private root-only UNIX-socket `kubectl proxy` carries metadata calls; no TCP
listener or copied kubecredentials are needed. The namespace Pod watch starts
at the initial full list resourceVersion. A foreign Pod (including a transient
one), watch error/gap, absent own-UID end-marker, changed PVC/PV binding or failed
UID-preconditioned helper cleanup prevents complete coverage. Helper intent and
UID evidence is retained in a failed peer's private stage; no automatic replay
or cleanup of a replacement UID is allowed.

Each retained PVC has its own UID-bound tar/hash and complete file-tree digest
(bytes, POSIX owner/mode, directories and contained links; external links and
special files are rejected). Verification extracts only into new Docker scratch
volumes and compares the extracted tree. Both PGDATA layouts must recover on a
fresh copy, produce a logical dump and pass a second strict SQL restore. Code
volumes follow the file-tree path and never start PostgreSQL. This does not
certify active non-database PVCs, extended filesystem attributes, or full
filesystem disaster recovery. No live PVC is writable or a restore target.

Unknown volumes, missing peers, unresolved generation deferrals and failed
queries are incomplete coverage. They never produce a successful full bundle.
Every declared core database volume must be present. A project database is
required once its controller metadata records role bootstrap, a machine manifest,
or an explicitly selected restored volume; a cell not yet materialized may have
only its core database. Artifact filenames must bind the exact manifest workspace,
kind and data-volume suffix before any scratch restoration can start.
`backup_cells.py --local-scope` is deliberately only local coverage; its external
publication obligations are closed by `runtime-backup.py` before `peers.tgz` and
the encrypted full bundle can be promoted. Partial local artifacts remain private.

The coverage scope is **platform and project databases**. Non-database runtime
PVCs are recorded as `non-database-pvc-not-copied`; this does not certify their
business files. The separate `max-backup` service covers `max-*` system databases.
Source repositories/objects and runtime configuration retain their existing full
bundle payloads. Classify and back up any additional durable business-file PVC
before claiming full filesystem disaster recovery.

## Install the delivered hooks

After the pushed revision has passed CI and canonical deployment, on Core:

1. Confirm the exact delivered `/opt/omnia` revision and that
   `yleum-platform-backup.service` is inactive. Preserve the current two hook
   files, their owners/modes and hashes in a private maintenance directory.
2. Install `platform-backup-entrypoint.sh` as
   `/usr/local/sbin/yleum-platform-backup` and `cells-backup-wrapper.sh` as
   `/usr/local/sbin/yleum-cells-backup`, both root:root, mode 0755. Verify the
   installed hashes against the delivered files. Keep the existing timer/unit;
   verify its effective ExecStart selects the canonical entrypoint.
3. Preserve all old ciphertext copies and keep the private restore key offline.
   The hook reads only the delivered public certificate at
   `/opt/omnia/infra/backup/offhost-backup-cert.pem`. A new public certificate does
   not make old encrypted copies decryptable with the new private key.
   The installed entrypoint defaults `BACKUP_PRUNE_ENABLED=false`, preserving
   existing local directories and MinIO prefixes. Enabling expiry requires an
   explicit operator decision (`BACKUP_PRUNE_ENABLED=true`, positive
   `RETENTION_DAYS`); invalid policy values fail before taking a backup. Backup
   preservation directories are never considered timestamped expiry targets.
4. Verify root's strict-host-key SSH aliases `commerce` and `runtime`, their
   noninteractive sudo, and the runtime `/usr/local/bin/kubectl` context `default`.
   The Commerce dump runs as `zeuszcz`; only a new private staging directory is
   chowned. Existing controller journals/credentials and permissions never change.
5. Run the canonical entrypoint once, then require exit 0, both `cells.tgz` and
   `peers.tgz`, `COVERAGE.json` with all three nodes, exact published DB coverage,
   and all encrypted bundle/checksum checks. A partial exit must remain failed.
   Do not replay an uncertain interrupted collector until its own process and
   temporary resources have been reconciled.

`BACKUP_PYTHON` selects the real interpreter for the root collector/verifier;
`CELLS_PYTHON` may select the strict controller-user wrapper. These interfaces are
different. Do not pass collector or verification arguments to the dump wrapper.

Exact hook installation (root maintenance shell, after replacing the revision):

```sh
set -euo pipefail
expected_revision='<full pushed and deployed 40-character revision>'
test "$(git -C /opt/omnia rev-parse HEAD)" = "$expected_revision"
! systemctl is-active --quiet yleum-platform-backup.service
preserved=$(mktemp -d /opt/omnia-runtime/backups/.hook-preservation.XXXXXX)
chmod 0700 "$preserved"
for name in yleum-platform-backup yleum-cells-backup; do
  path=/usr/local/sbin/$name
  if test -e "$path"; then
    test -f "$path" && test ! -L "$path"
    cp -a -- "$path" "$preserved/$name"
    sha256sum "$path" >> "$preserved/before-sha256.txt"
    stat -c '%n %u %g %a' "$path" >> "$preserved/before-owner-mode.txt"
  else
    printf '%s\n' "$name" >> "$preserved/previously-absent.txt"
  fi
done
systemctl cat yleum-platform-backup.service > "$preserved/unit-before.txt"
install -o root -g root -m 0755 /opt/omnia/infra/backup/cells-backup-wrapper.sh /usr/local/sbin/yleum-cells-backup
install -o root -g root -m 0755 /opt/omnia/infra/backup/platform-backup-entrypoint.sh /usr/local/sbin/yleum-platform-backup
cmp /opt/omnia/infra/backup/cells-backup-wrapper.sh /usr/local/sbin/yleum-cells-backup
cmp /opt/omnia/infra/backup/platform-backup-entrypoint.sh /usr/local/sbin/yleum-platform-backup
sha256sum /usr/local/sbin/yleum-{platform,cells}-backup > "$preserved/installed-sha256.txt"
systemctl show -p ExecStart yleum-platform-backup.service
printf 'HOOK_PRESERVATION=%s\n' "$preserved"
```

No existing config, state, controller credentials or their ownership are changed.
The new entrypoint overrides the old `CELLS_PYTHON` wrapper setting and reads the
current repository public certificate. Before restoring a preserved hook, require
the service inactive and both installed hooks still match `installed-sha256.txt`;
then copy back only the preserved files with `cp -a`. For a previously absent hook,
remove only the exact newly installed regular file whose hash still matches the
installation receipt. Keep that preservation directory and all backup copies.
Do not restart a rolled-back bootstrap hook or label it healthy: repair its
canonical-source/certificate/coverage contract first. Rollback does not undo key
rotation and cannot recover old ciphertext without the corresponding old key.

## Restore evidence and its limits

New bundles include checksum-bound `platform-globals.sql.gz` and
`projects-globals.sql.gz`. These contain original database roles and grants and
are sensitive payloads, subject to the same private staging and CMS encryption
as runtime configuration. Restore them into a disposable cluster initialized
with a unique bootstrap administrator before loading their respective database
dumps. Never print their SQL or substitute permissive placeholder roles.

`restore-test-omnia.sh` creates fresh unique scratch databases/containers only.
Full-cluster SQL restores use a new scratch bootstrap administrator and
`ON_ERROR_STOP=1`; SQL/data failures cannot pass just because table counts match.
The fresh cluster retains initdb's empty `postgres` and `template1` databases,
which `pg_dumpall` expects for its reconnects; the random bootstrap role avoids
colliding with the original `postgres` role restored from the dump.
Every Core/Commerce/runtime dump must restore. Missing archives, verifiers or
coverage fail the check. Scratch cleanup removes only resources created by that
test. No live database is a restore target.

`RESTORE_TEST.json` binds the verdict to `backup_sha256` and `backup_timestamp`.
It records the precise scope and `coverage_complete`. This server-side check
restores local plaintext payloads and validates the encrypted envelope; it does
**not** decrypt that envelope. It records `ciphertext_restore_verified:false`.
Independent encrypted-copy recovery requires the offline private key and remains
unproven until an isolated off-host decrypt-and-restore has succeeded.

For an off-host database acceptance run, first verify the downloaded CMS against
`OFFHOST_SHA256` and decrypt it on the private-key holder's computer. Never copy
the private key onto a production or restore server. Pass only the decrypted
archive to a disposable local Docker engine with cached, digest-pinned images:

```sh
DOCKER_HOST=unix:///run/disposable-recovery.sock python infra/backup/offline-restore.py \
  --bundle /private/decrypted.tgz --work-dir /private/new-restore-directory \
  --cells-script apps/orchestrator/scripts/backup_cells.py \
  --postgres-image 'postgres@sha256:<delivered digest>' \
  --receipt /private/new-receipt.json --disposable-sandbox
```

The receipt covers strict database restores, role recovery and platform/legacy
write-restart checks. Bind it externally to the CMS and plaintext SHA256 and the
delivered revision. MinIO object GET, current source witnesses and application
boot remain separate acceptance steps; `passed_checked_scope` alone is not full
filesystem disaster recovery. Retain the extracted data privately for those steps.

Physical SQL regression on an explicitly selected disposable Docker sandbox:

```sh
BACKUP_DISPOSABLE_DOCKER_TEST=1 CELL_POSTGRES_IMAGE='postgres@sha256:<delivered digest>' \
  uv run --frozen pytest -q tests/test_backup_restore_integration.py \
  tests/test_backup_cold_restore_integration.py
```

Run from `apps/orchestrator`; the test creates only fresh
`omnia-backup-scratch-*` resources, checks a real cluster restore, then verifies
that a SQL failure with the same table count is rejected. The cold cases cover
both PGDATA layouts, recovered rows and grants, and retained code bytes/owners/
modes, with exact scratch container/volume absence after cleanup. They are not
production restores or independent decrypt acceptance tests.

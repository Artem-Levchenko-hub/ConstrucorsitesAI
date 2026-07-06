# Fresh audit + execution log — 2026-07-05/06

From-first-principles audit (dead code + a re-derived path to the goal, ignoring
the old roadmap), then executed. This is the working record: what was cut, what
shipped, what's verified, and the **owner-gated** remainder with the exact
decisions/unblocks each needs.

Delivery is via `main` (`local == GitHub == VPS`), transferred to the prod repo by
git bundle over SSH (the VPS's GitHub creds are stale — see Operator notes).

---

## Part 1 — What was cut (dead code)

### Batch 1 — safe deletions, SHIPPED + deployed (`cleanup-2026-07-05`, `4078c65..9283cd0`)
Behaviour-identical removals: gateway `warmup.py`+`ENABLE_WARMUP` (pinged opus-4-8
every 4 min, hogged oneprovider's 1 slot — prod already ran it OFF) + unused
`default_model`; web `models.ts`/`MOCK_MODELS`/model-picker rudiments, `AnimeReveal`
+`animejs`, duplicate preset clients, `render-parity`; api orphan services
`admin_generator`/`preset_provenance`/`regression_registry` (0 callers); orchestrator
unread `StackSpec.name`. Gateway 59/59, web tsc clean, api import/collect clean.

### Batch 2 — provider-zoo collapse (gateway), SHIPPED + deployed (`cleanup-batch2-gateway-2026-07-05`, `c2d449c`)
All roles run `claude-opus-4-8` (verified: `ROLE_MODEL_MAP` + `DEFAULT_ROLE_MODEL`;
opus has no fallback entry). Deleted the Sber/Yandex providers and collapsed
`_LITELLM_MODEL_SLUG`/`_PROXY_ROUTES`/`vsegpt._VSEGPT_MODEL_SLUG`/`PRICE_TABLE`/
`_MODEL_META` to opus-4-8; `_FALLBACKS=[]`. Kept byte-identical: opus-4-8 via vsegpt
(fast) + oneprovider failover (`OPUS_VIA_VSEGPT`), whisper via proxyapi, flux image
gen (own registry). Gateway 59/59; **live opus completion smoke OK** on prod.

### Deliberately KEPT (adversarial verify overturned the audit's "delete")
- **`chip_causality`** — prod-dead but a live `test_pillar2_compose` uses it to cover
  real onboarding services.
- **Templates** `bare-nextjs`/`fastapi-postgres`/`telegram-bot-aiogram` — reachable via
  the stack registry / gated flag; **`vite-react-spa`** is the *most-frequent* path
  (just missing a `Dockerfile.prod` — build it, don't delete). `stack_registry.STACKS`
  is load-bearing for tests. `role_gate` is an honest no-op flag.
- **api `MODEL_TIER_MAP` + `vendor_profiles`** — NOT dead: DEFAULT/GENERIC-safe
  calibration tables with tests asserting gpt/gemini/yandex/sber mappings; "ready for
  future vendors". Trimming breaks tests for no runtime gain. ⇒ "collapse to one stack"
  ≠ delete working templates or safe calibration data.
- Still-inert-but-cosmetic (left as documented seams): api `_EMPTY_RESPONSE_FALLBACKS`,
  `models_router._MOCK_MODELS` (dev-mock only; prod proxies the gateway), web
  `types.ts` provider enum.

---

## Part 2 — What shipped toward the goal

### Step 0 — Survivability (the plan's #1 existential risk), SHIPPED (`5ac39f5`)
`infra/backup/backup-omnia.sh` — consistent `pg_dump` of the platform DB (`omnia`) +
ALL per-project schemas (`omnia_users`) + tar of `/opt/omnia-runtime/projects`;
size-guarded, sha256, 14-day retention, opt-in off-host rsync. `restore-test-omnia.sh`
loads the latest dump into a SCRATCH DB (live untouched). **Installed as VPS cron
`15 3 * * *`; verified live (restore-test: 9 tables OK).**
- **GAP (owner):** `BACKUP_OFFHOST_DEST` unset → LOCAL-ONLY. A disk failure still loses
  it. Set an off-host rsync/S3 target in the cron line for real disaster resilience.

### Step 3 — Isolation-as-product, first increment SHIPPED (`9239288`, flag `use_build_attestation`)
`services/attestation.py` assembles a canonical, sha256-digested (tamper-evident)
record of the FINAL runtime-gate verdicts per build; `overall_passed` is False on an
empty gate set. Wired best-effort at the gate-loop settle point in `messages.py` →
emits `[ATTEST] {json}` to the worker log (try/except — never fails a build). Verified
in the prod container. Honest scope: a content DIGEST, not a PKI signature; reflects
only gates that ran (the two-tenant A-vs-B proof is still agent-driven).

### Step 4 — Platform holes, first item SHIPPED (`901d123`+`fb94fc3`, flag `allow_stub_topup`)
`POST /api/wallet/topup` credited the caller's OWN wallet by a user-supplied amount
with no payment (self-credit → free unlimited generation once auth is open). Now gated,
**secure by default (403)**; prod keeps closed-beta free credits via
`ALLOW_STUB_TOPUP=true` (compose default) and **closes the hole with
`ALLOW_STUB_TOPUP=false` in prod `.env` + api restart** once YooKassa lands.

---

## Part 3 — Verified findings
- **Platform-side cross-tenant isolation is app-level SOLID.** Every authenticated
  resource endpoint (`/{project_id}/…` for snapshots, leads, messages, runtime,
  uploads, rollback, …) verifies ownership via a helper (`_project_owned_by` /
  `_owned_project` / `_ensure_owner`); `get_snapshot` even checks `snapshot.project_id`.
  No IDOR found (topup was a different class — a self-credit, not a resource leak).
- **The moat gap remains** (per [[omnia-verification-moat]]): the positive two-tenant
  A-vs-B proof inside GENERATED apps is agent-driven, gates are advisory
  (`ACCEPTANCE_SCORE_ONLY`), no DB-level RLS in generated drizzle apps, and the
  attestation is not yet deploy-gating.

---

## Part 4 — Remaining roadmap (OWNER-GATED — why each needs you)

Ordered; each is blocked on a decision, supervised rollout, or an external unblock.

1. **Finish Step 3 (isolation fail-closed) — the product.** Persist the attestation
   (Snapshot column / new table), make the two-tenant probe UNCONDITIONAL, gate DEPLOY
   on `overall_passed`, then flip gates FAIL-CLOSED. *Why supervised:* the plan warns a
   fail-closed flip may expose how often generated apps leak → pass-rate can drop; needs
   monitoring + targeted-fix-not-dead-end, not a blind autonomous flip.
2. **Close `/topup`** — set `ALLOW_STUB_TOPUP=false` when YooKassa is live / beta ends
   (*your timing call* — it removes beta free credits).
3. **Step 4 hardening:** RLS on the leads/platform tables (a migration + per-request
   tenant session-var + FORCE RLS + an INSERT policy for the public no-auth lead write —
   breaks the «Заявки» inbox or public capture if any part is off → supervised), JWT
   revocation, sandbox hardening (`container_harden`/egress — dark until host prep).
4. **Step 2 — crash-resilient worker loop** (persist each agent step → resume from last;
   snapshot-before-deploy + atomic rollback). Deep surgery in the live build loop.
5. **Step 5 — YooKassa vertical** (one business type, real ₽, funnel prompt→TLS-URL→lead).
6. **Step 6 — one-command export** (sources+schema+seed → `docker compose up` identical,
   isolation-passing app on another machine; export refuses a build without attestation).

### Unblocks needed from you
- **Off-host backup target** (`BACKUP_OFFHOST_DEST`) — makes backups disaster-proof.
- **`workflow`-scoped GitHub token** — to activate CI (`infra/ci/github-actions-ci.yml`);
  the deploy PAT lacks the scope, so the workflow file can't be pushed.
- **Refresh the VPS's GitHub credential** — the documented `git fetch/merge` deploy flow
  is broken there; delivery currently rides a git-bundle-over-SSH workaround.

---

## Operator notes (reversible flags + ops)
- `OPUS_VIA_VSEGPT` (gateway) — `false`=oneprovider failover (current, vsegpt dry, ~71s,
  concurrency≈1), `true`/unset=fast vsegpt (~3s). Fix = top up vsegpt then flip in `.env`.
- `ALLOW_STUB_TOPUP` (api) — `.env`=`false` closes the self-credit hole (env-only).
- `USE_BUILD_ATTESTATION` (api) — `false` silences `[ATTEST]` (env-only). Grep attestations:
  `docker logs omnia-prod-worker | grep '\[ATTEST\]'`.
- Backups: `crontab -l | grep backup-omnia`; manual `restore-test-omnia.sh` to prove a dump.
- Deploy = git-bundle → VPS `git fetch <bundle> main && git merge --ff-only FETCH_HEAD` →
  `cd apps/llm-gateway/deploy/full && docker compose up -d --build <svc>` (project `full`,
  NEVER `infra/`). Rollback tags: `pre-cleanup-2026-07-05`, `cleanup-2026-07-05`,
  `cleanup-batch2-gateway-2026-07-05`.

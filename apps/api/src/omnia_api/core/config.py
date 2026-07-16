import hashlib
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    env: str = Field(default="dev")
    log_level: str = Field(default="INFO")

    database_url: str
    database_test_url: str | None = None

    redis_url: str = Field(default="redis://localhost:6379/0")

    minio_endpoint: str = Field(default="localhost:9000")
    minio_access_key: str = Field(default="omnia")
    minio_secret_key: SecretStr = Field(default=SecretStr("omnia-secret"))
    minio_secure: bool = Field(default=False)
    minio_bucket_projects: str = Field(default="projects")
    minio_bucket_previews: str = Field(default="previews")
    # Bucket for AI-generated images (gpt-image-1 via gateway). Created lazily
    # on first image upload — see services/image_resolver.py:_ensure_bucket().
    minio_bucket_images: str = Field(default="omnia-images")
    minio_public_url: str = Field(default="http://localhost:9000")

    # Stock photography for `<img data-omnia-photo="keywords">` tags — real
    # thematic photos, not AI-generated. "off" (default) leaves the feature
    # dormant: the resolver strips unresolved photo tags so the section's flat /
    # mesh fallback shows (no broken images). "pexels" → Pexels API (blocked from
    # the RU prod egress — needs PEXELS_PROXY). "openverse" → Openverse API
    # (reachable from prod; free CC0/PD stock, no key needed). Each result caches
    # into `minio_bucket_photos` — one upstream hit per unique keyword, never per
    # render.
    photo_source: Literal["off", "pexels", "openverse"] = Field(default="off")
    pexels_api_key: SecretStr | None = Field(default=None)
    # Outbound proxy for Pexels calls only. pexels.com is unreliable / blocked
    # from the RU prod egress, so the api container reaches it via this proxy
    # (e.g. "http://user:pass@host:port"). Empty = direct. Applies ONLY to the
    # image_resolver Pexels client, never to the gateway / gpt-image path.
    pexels_proxy: str | None = Field(default=None)
    # Openverse (https://api.openverse.org) — free CC stock for `data-omnia-photo`,
    # reachable from the RU prod egress where Pexels/Unsplash are blocked. Anonymous
    # search works (low rate ~5/hr, 100/day); MinIO caches one fetch per UNIQUE
    # keyword so volume stays modest. For prod register a free app and set the
    # client id/secret (POST /v1/auth_tokens/register/) → an OAuth token lifts the
    # limit to ~100/min, 10k/day. `openverse_license` keeps picks commercially
    # usable WITHOUT attribution (default cc0,pdm = CC0 + Public Domain Mark).
    openverse_client_id: str | None = Field(default=None)
    openverse_client_secret: SecretStr | None = Field(default=None)
    openverse_license: str = Field(default="cc0,pdm")
    minio_bucket_photos: str = Field(default="omnia-photos")

    jwt_secret: SecretStr
    jwt_algorithm: str = Field(default="HS256")
    jwt_ttl_days: int = Field(default=7)
    jwt_cookie_name: str = Field(default="omnia_session")
    jwt_cookie_secure: bool = Field(default=False)
    # Production: ".omniadevelop.ru" — cookie visible on landing.* and app.*
    # subdomains so the sign-in performed on app.* is also recognised by the
    # marketing site (used by future "log out everywhere" / "switch account"
    # surfaces on the landing). Leave unset in dev — browsers reject explicit
    # `.localhost` domains and fall back to the request host anyway.
    jwt_cookie_domain: str | None = Field(default=None)

    llm_gateway_url: str = Field(default="http://localhost:8001")
    mock_llm: bool = Field(default=True)

    # V2 orchestrator (apps/orchestrator on :8003). Internal-only API behind
    # a shared-secret header — token MUST match the one in the orchestrator's
    # /opt/omnia-runtime/.env.orchestrator file.
    orchestrator_url: str = Field(default="http://localhost:8003")
    orchestrator_internal_token: SecretStr | None = Field(default=None)

    # GitHub OAuth — "Push to GitHub": user authorizes once, we store a per-user
    # access token (Fernet-encrypted at rest, key derived from jwt_secret) and push
    # the project's files into a repo on their account. Register an OAuth App at
    # github.com/settings/developers; client id/secret come from env (never committed).
    github_client_id: str | None = Field(default=None)
    github_client_secret: SecretStr | None = Field(default=None)
    github_callback_url: str = Field(default="http://localhost:8000/api/github/callback")
    github_oauth_scope: str = Field(default="repo")
    web_base_url: str = Field(default="http://localhost:3000")

    cors_origins: str = Field(default="http://localhost:3000")

    initial_wallet_balance_rub: float = Field(default=100.0)

    # Rate limiting (slowapi) on the costly generate/edit endpoint. Keyed per
    # authenticated user (valid JWT) and falling back to the real client IP, so a
    # single actor can't flood expensive LLM builds and drain balance / DoS the
    # box. Tunable without a code change; set rate_limit_enabled=false to disable.
    rate_limit_enabled: bool = Field(default=True)
    prompt_rate_limit: str = Field(default="20/minute")

    # Phase B — multipass design generation for budget models.
    # Env override for the multipass router. By DEFAULT (empty value) every
    # budget-tier model (CHEAP_MODELS — currently Haiku, Nano) is routed
    # through the 4-pass pipeline (skeleton → content → visual → assembly)
    # automatically — that is the "make cheap models look enterprise" path.
    # Special values:
    #   ""                            — default ON (= CHEAP_MODELS)
    #   "off" / "none" / "disabled"   — kill switch, single-shot for everyone
    #   "<csv-of-model-ids>"          — ADDS these to CHEAP_MODELS (union)
    # Use a kill switch only for debugging the single-shot path; production
    # value should stay empty so new budget models get multipass for free.
    multipass_models: str = Field(default="")

    # Phase L3 — JSON IR + Section catalog feature flag.
    # When True, the multipass pipeline replaces `pass_assembly` (full LLM
    # HTML rewrite) with `sections.render_page(ir)` — deterministic Jinja
    # rendering of validated `PageIR`. The model only emits JSON; the
    # renderer turns it into HTML. Saves ~50% tokens per generation +
    # eliminates omnia-kit class hallucination + locks visual ceiling.
    # Default ON — catalog/IR is now the standard generation path for everyone
    # (role-orchestration era). The model emits validated JSON; the renderer
    # turns it into HTML deterministically. Kill switch: USE_SECTION_CATALOG=false.
    use_section_catalog: bool = Field(default=True)

    # Phase L7 — Director→Polish 2-pass for premium tier (Opus / Sonnet /
    # GPT-5) on top of catalog mode. Pass 1 ("Director") emits the
    # structural PageIR with short placeholder headlines; pass 2
    # ("Polish") takes that IR and rewrites every text field with real
    # content (full headlines, real numbers in ₽, real names, cities).
    # Default ON — Director→Polish is the standard catalog path. Director
    # (role `director`, model Opus) picks structure; Polish (role `polish`,
    # model DeepSeek) writes the real content. Per-role models come from
    # ROLE_MODEL_MAP, not the user. Kill switch: USE_DIRECTOR_POLISH=false.
    use_director_polish: bool = Field(default=True)

    # Phase N+ — optional image-prompt enrichment. When True, a SHORT/weak
    # `data-omnia-gen` prompt (under IMAGE_PROMPT_MIN_WORDS words) is expanded
    # into a detailed photo brief by the `image_prompt` role (cheap Haiku)
    # before hitting gpt-image-1. Detailed prompts (the common case — the
    # generator is instructed to write "subject, scene, style, lighting,
    # angle, lens") skip enrichment, so this rarely fires. Fail-soft: any
    # enrichment error falls back to the original prompt. Kill switch:
    # USE_IMAGE_PROMPT_ENRICHMENT=false.
    use_image_prompt_enrichment: bool = Field(default=True)

    # Kill switch for AI image generation (data-omnia-gen → gpt-image via the
    # gateway → MinIO). When the gpt-image upstream is down (proxyapi balance dry
    # / OpenAI unreachable from the RU egress) every image call burns its full
    # per-image timeout, and a page with many image tags stalls the WHOLE build
    # for minutes before any snapshot is created — the preview "loads forever".
    # Turn OFF on prod while image-gen is dead: the resolver then strips
    # data-omnia-gen tags immediately and the section's CSS/mesh background shows
    # instead of a broken <img>. Flip back to true once the gpt-image route is
    # funded/reachable. Belt-and-suspenders: image_resolver also enforces an
    # overall deadline even when this is True.
    use_image_gen: bool = Field(default=True)

    # Which image model the resolver requests from the gateway for
    # `data-omnia-gen` tags. Served via the vsegpt provider (same key as the
    # chat models). Owner pick 2026-06-02: img-flux/flux-2-klein-4b — fast (~6s),
    # cheap, clears the vsegpt per-query limit. Graphic richness comes from the
    # art-director prompt (themed photo backgrounds + designed graphic layers per
    # section), not the image tier. Switch to flux-2-pro / nano-banana-pro (needs
    # a higher vsegpt per-query limit) via IMAGE_GEN_MODEL env — no code change.
    image_gen_model: str = Field(default="img-flux/flux-2-klein-4b")

    # Cost control (2026-06-05) — max UNIQUE AI image generations per build.
    # Repeated cards (menu / product / portfolio tiles) sharing a
    # data-omnia-gen-group collapse to ONE generation; concept/hero images keep
    # generating uniquely. Effective prompts beyond this budget REUSE an
    # already-made image (never generate more, never ship broken). Lower it to
    # spend less. Env: IMAGE_GEN_MAX_UNIQUE.
    image_gen_max_unique: int = Field(default=8)

    # Live image drop-in (2026-06-06) — emit a per-image `image.resolved` WS
    # event as each generated picture finishes, so the streaming preview can
    # swap it into its frame in real time (the "фотки въезжают в рамки" effect)
    # instead of all images appearing at once on the final snapshot. Purely
    # additive (extra events); OFF kills the live signal, images still land on
    # the committed snapshot. Env: USE_LIVE_IMAGE_EVENTS.
    use_live_image_events: bool = Field(default=True)

    # Visual enricher — post-process pass that injected decorative layers
    # (mesh / blob / SVG dot-grid / diagonal-lines / waves) into every bare
    # <section>. Built as a Haiku-era crutch against "flat AI sites", but it
    # cycled the variants mechanically across ALL sections, so the output read
    # as generative AI-slop (owner 2026-05-31: «откуда полоски/точки … ужасно»).
    # Owner-call: off completely. OFF by default so a lost prod-.env line cannot
    # silently revive the patterns; set USE_VISUAL_ENRICHER=true to re-enable.
    use_visual_enricher: bool = Field(default=False)

    # Signature-moment floor (2026-06-05) — post-process SAFETY NET that
    # guarantees every static build carries ONE "expensive" scroll moment
    # (.pin-stage / .compare / .omnia-draw / .scroll-clip-reveal). The
    # art-director is contracted to add one; this injects a single content-free
    # .omnia-draw line-art divider ONLY when the page has none. Surgical and
    # palette-agnostic (unlike the disabled per-section enricher above) → ON by
    # default. Kill per-env with USE_SIGNATURE_FLOOR=false.
    use_signature_floor: bool = Field(default=True)

    # Phase M — per-role model override. Empty = use ROLE_MODEL_MAP (topmix-v1)
    # below. CSV of `role=model_id` pairs, e.g.
    # "director=claude-opus-4-7,polish=deepseek-chat,audit=claude-sonnet-4-6".
    # Lets ops retune the price/quality mix per-role without a code deploy.
    role_models: str = Field(default="")

    # Hidden admin/debug override. When set (non-empty), forces THIS model id on
    # every pipeline role for every generation. Leave empty in prod — the role
    # orchestration (ROLE_MODEL_MAP) drives model choice. Users never see a
    # model picker; this env knob is the only manual override.
    force_model: str = Field(default="")

    # ── Phase 11 — freeform generation + acceptance gate ──────────────────
    # Plan: docs/plans/11-freeform-generation.md. Premium models write HTML
    # FREELY (no fixed Jinja templates); reliability comes from an acceptance
    # gate (render → check → self-repair) instead of locking the output shape.
    # Owner-call 2026-06-02: freeform + acceptance gate are the PRODUCT DEFAULT
    # now. Premium first-builds write bespoke HTML via Opus (role
    # `freeform_writer`) — the design-critical pass — and the acceptance gate
    # guarantees the page isn't broken (structure + no horizontal overflow),
    # repairs it, and falls back to the deterministic catalog/IR path if it
    # still fails. This is the "awwwards-from-first-prompt" path. The recent
    # art-director / designer-brain freeform brief (prompt_builder.py) only runs
    # when this is on — it was built but left dormant before this flip.
    # `use_vision_audit` is OFF (owner directive 2026-06-03, reverses 06-02): in
    # SCORE-ONLY mode the vision pass never gated anything — it only spent a paid
    # gateway vision call to label the page, AND it judged the acceptance-capture
    # screenshot, which is shot at `domcontentloaded` + 600ms and never waits for
    # the resolved (remote MinIO) images to paint → it saw gray placeholders and
    # cried "generic" on pages that look fine live. Net: paid noise. Killed; we
    # maximise brief-adherence at the WRITER instead (art_director_writer.py).
    # Fail-soft remains: if ever re-enabled, any error degrades to skipped (10).
    # Override any flag per-env in .env.
    #
    #   use_freeform_render  — premium tier writes free HTML (else catalog/IR)
    #   use_acceptance_gate  — run structure+responsive (+vision) check & repair
    #   use_vision_audit     — let the gate screenshot → vision model for a
    #                          "broken / generic / beautiful" verdict (needs
    #                          gateway multimodal support; best-effort, fail-soft)
    use_freeform_render: bool = Field(default=True)
    use_acceptance_gate: bool = Field(default=True)
    use_vision_audit: bool = Field(default=True)
    # Design judge (2026-06-05) — premium / on-button Awwwards critic. Runs ONE
    # vision-critic pass + at most ONE repair re-roll (cost-bounded — owner
    # directive: judge must NOT loop many times), then ship. When on, forces the
    # vision pass and lets the DESIGN verdict drive exactly one re-roll even in
    # score-only mode. ON by default (owner 2026-06-05: judge EVERY build — the
    # 1-iteration cap keeps cost bounded). Prereq fixed here: capture now waits
    # for images to paint (preview.py) so the judge sees real photos, not the
    # gray placeholders that made the PRIOR vision judge useless. Kill per-env:
    # USE_DESIGN_JUDGE=false.
    use_design_judge: bool = Field(default=True)

    # Build Plan + Coverage gate (owner directive 2026-06-30 «эскиз перед стройкой,
    # не на зелёном минимуме»). `use_build_plan` runs a planner pass (role
    # `planner` → Opus) BEFORE the agent build that emits a bounded feature spec
    # (screens/entities/capabilities), persists it in
    # projects.discovery_spec['build_plan'], and injects it into the build prompt
    # as an explicit checklist (the agent builds the WHOLE plan, not a thin
    # green-compiling subset). `use_coverage_gate` then makes completion mean "the
    # plan's must-have capabilities actually return their expected status"
    # (coverage_gate via agent_probe), auto-continuing the build until covered or
    # `coverage_max_attempts` / token budget is exhausted — then ships an honest
    # "N of M" via app_errors. ON by default per owner; fully fail-soft (empty
    # plan or gate crash → EXACTLY today's behaviour). Env: USE_BUILD_PLAN /
    # USE_COVERAGE_GATE / COVERAGE_MAX_ATTEMPTS.
    use_build_plan: bool = Field(default=True)
    # OFF after the 2026-06-30 live prod smoke: coverage probed a real messenger
    # build, FAILed 1/6 and thrashed 3 heal rounds with no progress — false
    # positives (auth-path caps like `register` 409 against the gate's own probe
    # user; write caps 400 on planner-guessed bodies; realtime already covered by
    # functional_gate). Re-enable after the fixes: drop auth-path caps, skip
    # coverage where a dedicated functional gate runs, treat 400 as advisory, and
    # log per-capability status. Build-plan (thesis 1) stays ON — it works.
    use_coverage_gate: bool = Field(default=False)
    coverage_max_attempts: int = Field(default=3)

    # BARE experiment (owner 2026-06-30 «отключим шаблоны, Opus строит с нуля» —
    # testing the hypothesis that the template substrate oppresses the model).
    # When ON, every CONTAINER-backed build is provisioned + built on the BARE
    # stack (templates/bare-nextjs: a blank runnable Next.js, no auth/db/entity-
    # engine/realtime/kit) with a minimal "build everything from scratch" prompt,
    # regardless of the discovery-recommended stack. Discovery/voting front is
    # unchanged. `orchestrator_template()` is the single chokepoint that swaps the
    # image+dir to `bare-nextjs`. Default OFF → templates as today; flip ON only
    # for the controlled comparison, then OFF. Env: BARE_BUILD_EXPERIMENT.
    # Back to False after the 2026-06-30 live test: bare/no-stack mode did NOT free
    # the model — the agent loop's explore/stall/cycle guards (tuned for the template
    # flow of write-files-not-bash) read Opus's bash-scaffolding as "looping" and
    # aborted at 13 steps with 0 files; the typecheck/smoke also assume Next (`tsc`).
    # The substrate + harness are one co-designed system — removing templates makes
    # the harness fight the model. Re-enabling bare needs the harness adapted too
    # (relax guards for bash-scaffolding, generic typecheck/smoke), not just a flag.
    # OFF (2026-06-30): the re-test confirmed bare/no-stack is NOT production-ready —
    # left ON it routed EVERY generation to a blank box where the agent scaffolds
    # from scratch (slow, off-target, the "ничего не выдаёт" the owner hit). Back to
    # the templates. The bare-mode harness fix (bash-as-work) + the bare-nextjs box
    # stay in the repo behind this flag for a future, properly-resourced attempt.
    bare_build_experiment: bool = Field(default=False)

    # Functional+security E2E gate (G004) — the ONLY gate that proves a feature
    # WORKS and does not LEAK (vs every other gate, which judges looks/structure).
    # Drives a live realtime-stack preview through the messenger north-star: two
    # members exchange a message live over SSE in <1s, and a non-member is denied
    # (403) the stream/history/publish — the behavioural proof for G001/G002 and
    # "secure from the first prompt". OFF by default (needs a live preview + the
    # realtime contract; advisory until wired into the ship boolean). Enable per
    # realtime project with USE_FUNCTIONAL_GATE=true.
    use_functional_gate: bool = Field(default=True)

    # Backend-authoring guardrail (G003) — lifts the "never write backend" ban and
    # replaces it with a static check: writer-authored server code may author real
    # logic but must go through the engine/SDK, never the DB raw (drizzle/pg/
    # @/lib/db outside the fixed engine — the only way to bypass owner/membership
    # scoping). Advisory by default; flip on to BLOCK ship on a raw-DB escape.
    use_backend_guardrail: bool = Field(default=False)

    # Multi-role enforcement gate (G007) — the entities-engine role-matrix check
    # (readRoles/writeRoles on /api/entities/<E>). NOTE: only the pure
    # `evaluate_matrix` core exists — there is NO live driver and NO call site, so
    # this flag is currently a NO-OP regardless of value. On the real-backend
    # (drizzle) path the same goal (a wrong user is denied) is covered by the
    # runtime isolation_gate. Wire a live `run_role_gate` (or drop the flag) before
    # relying on it. Kept True for the eventual entities driver.
    use_role_gate: bool = Field(default=True)

    # Transport-surface security gate (G005) — WIRED on the agentic path (realtime +
    # drizzle) via security_gate.run_security_gate through the blocking heal loop.
    # Captures the main route's response headers and BLOCKS only on product
    # guarantees / zero-false-positive invariants: X-Content-Type-Options=nosniff
    # present + CORS not wildcard-with-credentials. X-Frame-Options and the 413
    # payload cap are deliberately NOT blocked (preview-iframe embedding needs no
    # X-Frame-Options; the templates don't enforce 413) — see
    # security_gate.surface_verdict_from_headers.
    use_security_gate: bool = Field(default=True)

    # SAST gate (K3a, knowledge-layer plan §3.1) — deterministic STATIC source
    # scan of writer files for the top AI-code CWEs (injection sinks + hard-coded
    # secrets — CWE-89/78/94/798, the heaviest classes per arXiv 2510.26103).
    # `use_sast_gate` runs it + advisory-logs findings (sibling to the runtime
    # security_gate); `sast_gate_blocking` makes a finding a BLOCKING outcome that
    # the agent_gate_feedback loop heals before ship. Both OFF by default →
    # prod generation byte-unchanged. Env: USE_SAST_GATE / SAST_GATE_BLOCKING.
    use_sast_gate: bool = Field(default=True)
    sast_gate_blocking: bool = Field(default=False)

    # Build attestation (fresh-plan Step 3) — after the runtime gates settle, emit
    # a tamper-evident (sha256-digested) record of the FINAL gate verdicts to the
    # durable log stream ("[ATTEST] {...}"). Additive + best-effort: it changes no
    # gate behaviour and can never fail a build. The foundation for "deploy ↔
    # proven"; DB-persist + deploy-gating land in a follow-up. Env: USE_BUILD_ATTESTATION.
    use_build_attestation: bool = Field(default=True)

    # Deploy-attestation gate (fresh-plan Step 3 — "deploy ↔ proven"). At deploy the
    # api looks up the build's saved attestation and LOGS whether it's proven
    # ("[DEPLOY-GATE] … proven=…") — ADVISORY by default so we measure the real
    # pass-rate before blocking. `deploy_attestation_blocking` then makes an
    # unproven build REFUSE to deploy (409). Safe rollout: keep blocking OFF, watch
    # the log, flip DEPLOY_ATTESTATION_BLOCKING=true when ready. NB projects built
    # before attestations have none → count as "not proven" once blocking is on.
    # Env: USE_DEPLOY_ATTESTATION_GATE / DEPLOY_ATTESTATION_BLOCKING.
    use_deploy_attestation_gate: bool = Field(default=True)
    deploy_attestation_blocking: bool = Field(default=False)

    # Wallet self-top-up (MVP stub) — POST /api/wallet/topup credits the caller's
    # OWN wallet by a user-supplied amount with NO payment. Fine for closed beta
    # (free credits), a self-credit hole in the open (free unlimited generation).
    # SECURE BY DEFAULT (False -> 403); prod keeps the beta behaviour via explicit
    # ALLOW_STUB_TOPUP=true and flips it OFF (env-only, no redeploy) once real
    # YooKassa payment lands (fresh-plan Step 5) / beta ends. Env: ALLOW_STUB_TOPUP.
    allow_stub_topup: bool = Field(default=False)

    # Skill injection (K1, knowledge-layer plan §2) — when on, the agent's system
    # prompt for a container build is composed with the stack's .omnia/skills
    # (security/a11y/perf canons aligned with the gates), raising the first
    # draft's floor. OFF by default → the agent prompt is unchanged. Env:
    # USE_SKILL_INJECTION. (Effect is unproven on its own — validate via A/B vs
    # the gates per the plan; the gates remain the guaranteed ceiling.)
    use_skill_injection: bool = Field(default=True)

    # Full runnable export (P5, knowledge-layer/master plan) — when on, the
    # project download for a CONTAINER stack overlays the skeleton template tree
    # under the generated files so the zip is a RUNNABLE repo (skeleton + your
    # code + README), not just the generated files. Fail-soft: if the template
    # tree isn't available it falls back to today's snapshot-only zip, so it can
    # never produce a worse download. OFF by default. Env: USE_FULL_CONTAINER_EXPORT.
    use_full_container_export: bool = Field(default=True)

    # Clarify interview (2026-06-05) — on the FIRST message of a brand-new
    # project, ask the user 3–4 short business-specific questions BEFORE building
    # (precise brief → точечнее сайт). Their answers (next message) drive the
    # real build via history. Fires only when the project has zero prior messages
    # and no real snapshot yet; the user can reply "генерируй" to skip. ON by
    # default. Kill per-env: USE_CLARIFY_INTERVIEW=false.
    use_clarify_interview: bool = Field(default=True)
    # Progressive discovery (2026-06-09, owner P1 — zero-friction onboarding).
    # Supersedes BOTH the blocking onboarding quiz (removed client-side) AND the
    # one-shot 3-4-question clarify above. On a brand-new project the assistant
    # runs a CONVERSATIONAL discovery: it asks ONE short elementary question at a
    # time, adapts to answers, and decides on its own when it has enough — then
    # compiles a brief, recommends a stack, and builds. When on, it takes over
    # the first-build interview and the legacy `use_clarify_interview` path is
    # bypassed. Kill switch for instant rollback to the batch clarify (R-10):
    # USE_PROGRESSIVE_DISCOVERY=false.
    use_progressive_discovery: bool = Field(default=True)
    # Batch discovery (2026-06-14, owner rule 13 #1 — NORTH STAR pillar 2). Within
    # progressive discovery, plan the WHOLE set of 3–4 product-tailored questions
    # in ONE upfront gateway pass (right after the first prompt), persist them on
    # `Project.discovery_plan`, then serve one per turn with NO further gateway
    # call — instant between steps, no per-question minute-long wait, and every
    # question is about the user's actual product (not a generic "тип сайта"
    # timeout fallback). Kill switch (R-10): USE_BATCH_DISCOVERY=false reverts to
    # the per-question conversational discovery.
    use_batch_discovery: bool = Field(default=True)
    # Async onboarding (2026-07-01, owner). The batch-discovery plan is ONE Opus
    # call and Opus via oneprovider runs ~60-70s per call (forced extended
    # thinking) — far past the client's 30s POST /prompt budget, so the FIRST
    # prompt of a new project used to time out ("Request timed out after
    # 30000ms", 0 tokens). When on, the first-turn plan is computed OUT OF BAND:
    # POST returns instantly with a placeholder assistant turn, and the tailored
    # question batch (survey) is streamed in over the WebSocket when Opus answers.
    # OFF → the legacy synchronous plan (blocks the request). Env:
    # USE_ASYNC_ONBOARDING.
    use_async_onboarding: bool = Field(default=True)
    # Auto stack-routing (2026-06-09, owner P1 — last mile of zero-friction).
    # When progressive discovery decides to BUILD and recommends a container
    # stack (fullstack / nextjs_entities) for a still-static project, the server
    # flips the project's template to that stack, re-scaffolds its git from the
    # matching template, and provisions the orchestrator dev container — so the
    # user never has to pick a stack or hit «Запустить». Kill switch (R-10):
    # USE_AUTO_STACK_ROUTING=false (discovery still recommends in the brief, but
    # the project stays static — old behaviour).
    use_auto_stack_routing: bool = Field(default=True)
    # Follow-up app-ification (P-H1, 2026-06-21). A FOLLOW-UP on a STATIC project
    # that clearly asks to become a real app ("переделай в полноценное приложение:
    # вход, кабинет, база") escalates the stack static→container instead of
    # surgical-editing the flat page (the H1 blind spot). Non-destructive: flips
    # the template only (like the code→web pivot) — the static history stays
    # rollback-able and the orchestrated build writes the app on top. Default OFF:
    # the feature ships dark and is enabled (USE_FOLLOWUP_APPIFICATION=true) only
    # after live verification, so prod behaviour is unchanged until then.
    use_followup_appification: bool = Field(default=True)
    # ── Result-type router (RT-1, 2026-06-22) ────────────────────────────
    # First-class `result_type` (landing/web_app/tool/site/code) decided on the
    # FIRST prompt SEPARATELY from the stack, by a cheap LLM classifier (role
    # `result_type`) with the existing keyword nets as a deterministic safety-net.
    # Master switch: OFF → the first-build stack decision is byte-identical to
    # today (only the legacy _infer_* nets run). Kill: USE_RESULT_TYPE_ROUTER=false.
    use_result_type_router: bool = Field(default=True)
    # Sub-slice (independently flippable): a `landing` result-type with a conversion
    # word («запись/бронь/оформить заказ») builds as a PUBLIC lead-capture landing
    # (spa + POST /p/<slug>/lead) instead of being force-escalated to an auth-gated
    # nextjs_entities app behind /signin (BS-7). Requires use_result_type_router.
    # OFF → today's escalation. Highest-value, lowest-risk slice — flip FIRST.
    # Env: RESULT_TYPE_LANDING_LEAD_SINK=true.
    result_type_landing_lead_sink: bool = Field(default=True)
    # Raise app-ification framing into the FIRST build (bug 2): «сделай
    # веб-приложение / приложение / чтобы пользователи могли …» on the first prompt,
    # even without a precise backend noun, routes to web_app (nextjs_entities)
    # instead of falling through to spa. Requires use_result_type_router. OFF →
    # first build ignores framing (today). Env: RESULT_TYPE_FIRSTBUILD_APPIFY=true.
    result_type_firstbuild_appify: bool = Field(default=True)
    # ONE clarifying question about the RESULT TYPE when it is genuinely ambiguous
    # (classifier unsure AND keyword net silent). Folds "type clarity" into the
    # build-readiness gate so an ambiguous «приложение» earns one type question
    # instead of design questions about an unknown product. Requires
    # use_result_type_router. OFF → no extra question. Env:
    # RESULT_TYPE_CLARIFY_QUESTION=true.
    result_type_clarify_question: bool = Field(default=True)
    # App-error cards (2026-06-09, owner P2). After a container-app build, surface
    # build/compile/schema failures as structured cards in the chat (instead of
    # plain italic notices) and probe the dev server for a Next.js compile error
    # post hot-reload. OFF → original italic-text notices, no compile probe
    # (instant rollback, R-10). Env: USE_ERROR_CARDS=false.
    use_error_cards: bool = Field(default=True)
    # Max self-repair re-rolls before the gate gives up (and freeform falls
    # back to catalog). Each retry is one extra LLM call — keep small.
    acceptance_max_retries: int = Field(default=2)
    # Vision score (0..10) at or above which a page passes the gate.
    acceptance_min_score: int = Field(default=7)
    # SCORE-ONLY mode (owner 2026-06-02): run the gate to COMPUTE + publish the
    # vision verdict (visibility), but SHIP the freeform first attempt regardless
    # — no repair re-rolls, no catalog fallback. Repairs via the coder don't
    # escape "generic", and the catalog fallback ships a WORSE template than the
    # rich freeform page; this keeps the freeform page + the score, and makes
    # builds fast. Flip ACCEPTANCE_SCORE_ONLY=true to enable.
    acceptance_score_only: bool = Field(default=False)
    # Repair spend floor (2026-06-07, cost): with the design judge on, the gate
    # used to fire a full second writer pass whenever attempt-0 wasn't "passed"
    # (vision score < acceptance_min_score=7). The Awwwards critic rarely scores
    # ≥7 first try, so that ~37%-of-build repair ran on ~100% of builds — and the
    # best-so-far guard often reverted it anyway (pure waste, measured on prod).
    # Now the repair is spent ONLY on a GENUINELY deficient page: a hard
    # structural/responsive defect, a "broken" vision verdict, or a vision score
    # BELOW this floor. A merely-not-perfect page (struct+resp OK, score in
    # [floor, min_score)) ships as attempt-0 — first-pass quality is raised by a
    # sharper brief, not a reflexive re-roll. Set = acceptance_min_score (7) to
    # restore the old always-repair-on-borderline behaviour.
    acceptance_repair_floor: int = Field(default=5)
    # ── Taste barrier (область T) — re-arm the vision verdict as a FLAGGED gate ──
    # Since V1.6 the vision verdict (broken/generic/beautiful) is pure ADVISORY:
    # `acceptance.evaluate` drops `min_score` (`_ = min_score`) and computes
    # `passed` without it. The deterministic composition floor (taste/hierarchy)
    # catches "ugly by the numbers", but a page that is "not ugly, just generic"
    # (vision verdict=generic, score 5–6, struct+resp OK) clears everything and
    # ships. When True, the vision verdict gates `passed` AGAIN — but only when
    # vision REALLY ran (a skip/ABSTAIN scores 10 and never blocks, R-10): a page
    # blocks iff `verdict in {broken, generic}` OR `score < acceptance_min_score`.
    # Default OFF = byte-identical to today (vision stays advisory). Flip
    # ACCEPTANCE_VISION_BLOCK_ENABLED=true to make taste a real ship barrier.
    acceptance_vision_block_enabled: bool = Field(default=False)
    # Promote a "generic" vision verdict (not only "broken") to REPAIR-WORTHY in
    # the Loop A self-repair gate (messages.py): a merely-generic page earns one
    # re-roll with the vision issues as concrete feedback, so "not ugly but
    # generic" regenerates instead of shipping. Only fires when vision really ran.
    # Default OFF = today's behaviour (only broken / score<repair_floor re-rolls).
    acceptance_taste_repair_on_generic: bool = Field(default=True)
    # Taste-specific repair budget — how many extra Loop A re-rolls the taste
    # barrier may spend. Decoupled from the global `auto_regenerate_enabled`
    # (owner: never auto-regenerate the whole page) so the taste path can be
    # calibrated in isolation. ONLY raises `_max_acc` when
    # `acceptance_taste_repair_on_generic` is also ON. Default 0 = no taste
    # re-roll (today's behaviour); 1 = exactly one generic→repair pass.
    acceptance_taste_repair_passes: int = Field(default=0)
    # Phase 1 / Area D — soften the catalog fallback (anti-sameness, DARK). Today,
    # with `auto_regenerate_enabled` OFF, a freeform page that fails the gauntlet
    # gets ZERO repair re-rolls and drops STRAIGHT to the single catalog template —
    # so the harder the floors bite, the more diverse-but-rejected pages collapse to
    # the SAME fallback look. This budgets N freeform repair re-rolls with the
    # gate's own failed-class feedback BEFORE the catalog fallback fires, so the
    # rich (diverse) freeform page gets a chance to FIX the specific issue instead
    # of being wholesale-replaced by the template. Decoupled from
    # `auto_regenerate_enabled` (same discipline as `acceptance_taste_repair_passes`)
    # so it can be calibrated in isolation; the repair is a TARGETED feedback re-roll,
    # not a blind full-page regeneration. Default 0 = today's behaviour (no extra
    # re-roll; straight to catalog). Recommended flip: 1. Env: ACCEPTANCE_GATE_REPAIR_PASSES.
    acceptance_gate_repair_passes: int = Field(default=0)
    # OWNER 2026-06-14 — AUTOMATIC FULL-PAGE REGENERATION OFF (default False).
    # The owner saw a build auto-"перегенерирую с рабочими ссылками" mid-session
    # and ruled: NEVER auto-regenerate the whole page — only deterministic
    # inline/targeted edits. When False, both auto re-roll paths are suppressed:
    # (1) the dead-link LLM re-roll (the inline href fixer still runs — that's a
    # targeted edit, kept), and (2) the acceptance-gate repair re-roll (the gate
    # still EVALUATES and publishes its advisory verdict, but never re-rolls —
    # `_max_acc` is forced to 0). Flip to True only to restore auto-repair.
    auto_regenerate_enabled: bool = Field(default=False)
    # V1.6 keystone — the acceptance gauntlet (`accept_gauntlet.run`) is the ship
    # decision: `evaluate()` blocks on its findings and the vision verdict is
    # demoted to advisory. The DETERMINISTIC defect-registry leg always blocks
    # (cheap, pure, the known-defect ratchet — dead-auth-link, dark-theme-loss,
    # bad lucide imports, …). The RENDERED legs (wow-dom / perf-a11y / chip-pixel)
    # each spin up a headless browser, so on the product-default freeform path
    # they are OFF by default — flip ACCEPTANCE_GAUNTLET_RENDER_GATES=true to add
    # their teeth to the hot path. The standalone CLI / niche-E2E always runs the
    # full fan-out (it audits a live container URL, no per-attempt cost).
    acceptance_gauntlet_render_gates: bool = Field(default=True)
    # V1.6 14/5 — DECOUPLE the composition floor from the touch leg. Taste +
    # hierarchy (the COMPOSITION_LEGS) score awwwards richness at DESKTOP width and
    # have NO 44px-touch false-positive, so they are the ALWAYS-ON hard ship-block
    # on the product path — separate from the wow-dom touch leg (44px), which stays
    # behind `acceptance_gauntlet_render_gates` until calibration 11/5. Before this
    # flag the whole render-half was gated by one switch (default OFF), so the
    # pillar-1 awwwards promise was asserted on ZERO shipping requests. Default ON.
    acceptance_gauntlet_composition_gates: bool = Field(default=True)
    # V2.5.2 — chip-pixel = HARD ship-block (causality bridge). The chip-pixel leg
    # asserts request↔render fidelity from the user's persisted onboarding answers
    # (`projects.discovery_spec`, V2.5.0/V2.5.1). It has NO 44px-touch false-positive
    # and is INERT when there is no spec (asserts nothing → passes), so it runs
    # ALWAYS-ON — decoupled from `acceptance_gauntlet_render_gates` (which keeps the
    # wow-dom touch leg behind calibration 11/5). `evaluate()` switches it on only
    # when a non-empty spec exists, so a chip→pixel mismatch (dark+violet requested,
    # light+red rendered) hard-fails the ship path; projects without onboarding
    # answers are byte-identical to before (no extra render). Default ON; flip
    # ACCEPTANCE_GAUNTLET_FIDELITY_GATE=false to disable if a false-positive surfaces.
    acceptance_gauntlet_fidelity_gate: bool = Field(default=True)
    # V3.3 — the money-free COMPOSITION FLOOR (`compose=` dial). A pure source-scan
    # (no render, no model) that hard-fails a catastrophically flat freeform
    # `index.html` — one uniform type size, no section rhythm, no hero — BEFORE any
    # paid render or the advisory vision pass. The floors are catastrophe-only, so a
    # real enterprise generation never trips them, and the scan is INERT (a passing
    # no-op) on a set with no standalone HTML page (entity/fullstack stacks, judged
    # by the rendered taste/hierarchy legs). It runs ALWAYS-ON on the hot path —
    # decoupled from `acceptance_gauntlet_render_gates`. Default ON; flip
    # ACCEPTANCE_GAUNTLET_COMPOSE_GATE=false to disable if a false-positive surfaces.
    acceptance_gauntlet_compose_gate: bool = Field(default=True)
    # V1.13b — the pillar-1 CEILING leg (`REFERENCE_LEGS`). Where the composition
    # legs assert a FLOOR ("no defect class present"), this asserts a CEILING: a
    # generation must MEET OR BEAT a curated enterprise corpus on the five richness
    # axes the taste/hierarchy gates already score (R-04, no new metric). It is
    # decoupled from `acceptance_gauntlet_render_gates` via the `reference=` dial
    # and ABSTAINS (never hard-fails) when the corpus is empty or a page does not
    # render (R-10). The wiring + deterministic/live-chromium teeth are money-free
    # and shipped now.
    #
    # FLIP-RUNBOOK (V1.13c — the PAID owner corpus-run, made falsifiable). Do NOT
    # flip this to True by eye. The flip is permitted iff `scripts/
    # reference_flip_milestone.py --mode gate` exits 0, which proves all three:
    #   1. CANDIDATES CLEAR — N>=1 fresh generations (no model change, no manual
    #      edits) each MEET-OR-BEAT every curated corpus niche.
    #   2. ADVERSARY BELOW (teeth) — the known-mediocre baseline regresses on >= 2
    #      of the five richness axes against EVERY niche (`prove_reference_ceiling`).
    #   3. CORPUS PRESENT — the frozen reference corpus is non-empty.
    # Owner: generate the candidates, run `--mode gate`, then flip + commit the
    # `--out` report. CI runs `--mode guard` (via test_reference_flip_milestone):
    # this flag ON without a recorded passing milestone turns the suite RED. Mirror
    # of `acceptance_gauntlet_render_gates` / 16/5e. Default OFF.
    acceptance_gauntlet_reference_gate: bool = Field(default=True)
    # V1.13d — the CEILING RATCHET strength. The reference leg today compares only
    # the BOOLEAN per-axis verdicts (axis passed / failed), so once a generation
    # independently clears the taste/hierarchy floor it "meets or beats" every
    # curated reference trivially (True >= True) — the corpus adds no teeth beyond
    # the always-on composition floor. Flip this ON to enforce the real ceiling: a
    # generation must score within `reference_ceiling_tolerance` points of the
    # reference's OWN combined richness score (taste 0–5 + hierarchy 0–3), not just
    # match its boolean axes. OFF (default) → byte-identical boolean floor (current
    # behaviour). It is fail-soft: the score floor applies ONLY when BOTH pages
    # rendered AND the reference itself is rich (cleared its boolean floor); any
    # render miss or a thin reference ABSTAINS (R-10), so a flaky corpus render can
    # never sink an otherwise-good page. Independent of the `reference_gate` dial
    # (that decides WHETHER the leg runs; this decides HOW STRICT it is when it does).
    reference_ceiling_enforced: bool = Field(default=True)
    # Points a generation may fall below the reference's own richness before the
    # ceiling ratchet fails it (only consulted when `reference_ceiling_enforced`).
    # 0 = strict meet-or-beat; 1 (default) forgives one soft regression so a single
    # axis dip on a genuinely strong page is not a false block.
    reference_ceiling_tolerance: int = Field(default=1)
    # V1.17 — the catalog-realism ratchet (`CATALOG_LEGS`). The eight money-free
    # RULE-10 demo-seeder fixes (niche titles, price-bands, real images,
    # title↔category, title↔description, category synonyms, future dates, niche
    # emails) each shipped with a unit test, but the only gate over the seeder's
    # output, `data_gate`, measures MIN_ROWS>=6 — non-emptiness, never realism, so a
    # NEW niche could silently regress any class. This leg scores the rendered
    # catalog DOM across those realism axes (mirroring taste_gate's
    # JS-extract→Python-score shape, R-04). It is ADVISORY (a non-blocking
    # quality-card): it surfaces a 0–5 score + the fired axes in the gauntlet table /
    # subscore but NEVER blocks ship, and it ABSTAINS on a render miss / WAIVES a
    # non-catalog page (R-10), so wiring it is safe everywhere. Default OFF on the
    # hot path — it adds one headless render per generation, so the owner flips it on
    # (or the paid-run manifest folds it in) once the niche heuristics have earned
    # trust; the CLI folds it in already.
    acceptance_gauntlet_catalog_gate: bool = Field(default=True)
    # V1.6 16/5 — ENTITY/FULLSTACK hot-path. Entity apps skip acceptance.evaluate
    # (container-backed), so the composition floor never touched the dominant
    # pillar-1 class. This wires the live-URL path: after a clean hot-reload +
    # compile-settle, a worker job fans the COMPOSITION_LEGS over the LIVE
    # container (omnia-dev-<slug>:3000, container-to-container, no public egress)
    # and surfaces a hard_failed as a quality card.
    #
    # Default OFF (calibration-gated, same discipline as render-gates 14/5/0/5):
    # the FIRST live run against the canonical good entity app (sushi) hard-failed
    # taste — `font-pairing≥2` rejects single-family designs and the Next DEV
    # overlay (`__nextjs-Geist`) pollutes the DOM. A hot-path hard-gate would flood
    # false-positive cards on good apps. The mechanism is proven (renders, scores,
    # discriminates with precise classes); flip ON once the legs are calibrated for
    # real entity apps (strip dev-overlay nodes + taste single-family tolerance) —
    # carried as 16/5b. CLI / niche-E2E always run the legs regardless.
    acceptance_entity_composition_gate: bool = Field(default=True)

    # ── Phase 1 / Area D — composition-gate retune (anti-sameness, DARK) ───
    # Why: the ALWAYS-ON composition floor (taste 4/5 + hierarchy 2/3) mechanically
    # rewards ONE silhouette — a single towering hero, ONE dominant focal element,
    # a hero image, NO equal 3-card rows. Flat/swiss/modular/multi-focal/poster
    # layouts hard-fail, so diverse-but-valid generations are funnelled back to the
    # one "safe enterprise landing" (and a freeform fail then drops to the single
    # catalog template). These knobs let the owner DEMOTE the deviation-punishing
    # checks to advisory (they still surface in the gauntlet card, but no longer
    # block ship or feed repair) WITHOUT touching the real quality floor, which is
    # correctness/a11y (contrast/WCAG/dead-link/44px) + the catastrophe compose
    # floor — none of which these touch. Applied in `accept_gauntlet` at the
    # composition-leg verdict, so EVERY ship path (freeform + entity + CLI) honours
    # them uniformly; the pure taste/hierarchy rubrics are unchanged.
    #
    # ALL DEFAULTS = today's behaviour (byte-identical). Recommended flip values are
    # noted per flag; the owner enables them on the stand when ready (DARK rollout).
    #
    # Comma-list of HIERARCHY check ids to treat as ADVISORY (surface, never block).
    # Known ids: "type-dominance", "focal-dominance", "asymmetry". When non-empty
    # the hierarchy floor becomes "every NON-advisory (blocking) check passes" (the
    # 2/3 score model is bypassed). Recommended: "focal-dominance,asymmetry" — keep
    # only `type-dominance` as the floor, so modular grids / symmetric posters /
    # multi-focal layouts stop being rejected. Empty (default) → 2/3 score model.
    gate_hierarchy_advisory_checks: str = Field(default="")
    # Comma-list of TASTE check ids to treat as ADVISORY (surface, never block).
    # Known ids: "font-pairing", "type-scale", "hierarchy", "layout-variety",
    # "hero-imagery". When non-empty the taste floor becomes "every NON-advisory
    # check passes" (the score model below is bypassed). Recommended: "hero-imagery"
    # — stop forcing every page to carry a big hero image. Empty (default) → score
    # model with `gate_taste_min_score`.
    gate_taste_advisory_checks: str = Field(default="")
    # TASTE score floor (0..5) when no taste advisory checks are set. Default 4 =
    # `taste_gate.MIN_SCORE` (today). Recommended flip: 3 — loosen the single-shape
    # demand without dropping the floor to the bootstrap baseline.
    gate_taste_min_score: int = Field(default=4)
    # HIERARCHY score floor (0..3) when no hierarchy advisory checks are set.
    # Default 2 = `hierarchy_gate.MIN_SCORE` (today). Kept for symmetry; prefer the
    # advisory-checks knob above (more targeted than a blunt score drop).
    gate_hierarchy_min_score: int = Field(default=2)

    # ── Area C (authenticated cabinet gate) — DARK, default OFF ───────────
    # When ON the live-app gate LOGS IN to the generated app with a seeded
    # operator account and scores the real CABINET (/dashboard + CRUD) instead
    # of the public storefront `/`. It then fans the FULL rendered set
    # (first_paint, perf_a11y, wow_dom, data, taste, hierarchy) at desktop AND
    # @390, and verifies empty-state/onboarding/skeleton hygiene. OFF = current
    # behaviour byte-identical (taste+hierarchy on `/`, no login). Requires the
    # orchestrator's OMNIA_GATE_SEED=1 so a login-able seed account exists.
    gate_authenticated_cabinet: bool = Field(default=True)
    # Login email for the gate's seed operator account (matches the orchestrator
    # seed). Fixed local address — it can never receive mail, only sign in.
    gate_seed_email: str = Field(default="gate@omnia.local")
    # ADVISORY at first: the cabinet empty-state/onboarding/skeleton gate
    # surfaces a quality card but never blocks ship until calibrated (same
    # discipline as acceptance_gauntlet_catalog_gate).
    gate_cabinet_states_advisory: bool = Field(default=True)
    # Area C (b2) — Chromium host-resolver rule so the gate's headless browser can
    # reach a generated app's PUBLIC preview host (its canonical Auth.js AUTH_URL,
    # where secure cookies work) from inside the worker network. The worker can only
    # route to the host nginx by the docker-gateway IP, so we map the preview
    # wildcard → that IP; nginx terminates TLS (valid wildcard cert) and proxies to
    # the container with X-Forwarded-Proto=https, so the app sees its canonical https
    # origin and the credentials login + cabinet render succeed. Empty (default) →
    # the authenticated path is OFF (gate stays anonymous). Example value:
    # "MAP *.preview.lead-generator.ru 172.21.0.1". Env: GATE_PREVIEW_RESOLVER_RULES.
    gate_preview_resolver_rules: str = Field(default="")

    # ── Phase 11 — Sprint 4 (anti-generic) + Sprint 5 (rollout) ───────────
    # Originality: fingerprint each accepted freeform page and penalise the
    # next one that comes out near-identical to a DIFFERENT project's page
    # (the "every AI site looks the same" failure). Default ON (anti-generic) —
    # only active for freeform pages; catalog pages are intentionally alike.
    # OFF (owner 2026-06-03): like vision it fingerprints the acceptance-capture
    # screenshot — unreliable when remote images haven't painted — and in
    # score-only mode only labels, never blocks. Dead weight; killed with vision.
    use_originality: bool = Field(default=False)
    # Phase 1 / Area D — originality SHADOW metric (anti-sameness instrumentation).
    # `use_originality` (above) makes a near-duplicate BLOCK ship + feed repair —
    # which, with auto-regenerate off, just drops the page to the uglier catalog
    # fallback (owner kept it OFF). Shadow mode is the safe observability path: it
    # fingerprints the accepted page, MEASURES the nearest cross-project dHash
    # distance, LOGS it as a diversity metric, and REMEMBERS it in the pool —
    # but NEVER blocks ship and NEVER feeds repair. This quantifies how alike the
    # generated sites are, so each diversity phase (D→C→A→B+E) can be measured.
    # Independent of `use_originality`; default OFF = no extra work, no log. Flip
    # ORIGINALITY_SHADOW=true to start collecting the metric. Fail-soft (R-10).
    originality_shadow: bool = Field(default=False)
    # Hamming distance (0..64 over a 64-bit dHash) at/below which two pages are
    # "too similar". Lower = stricter. ~10 catches near-duplicates without
    # flagging merely same-vibe pages. Also the threshold the shadow metric logs
    # against (to flag near-duplicates without blocking).
    originality_max_distance: int = Field(default=10)
    # Gradual rollout: freeform applies to this % of projects (deterministic
    # bucket by project_id). 100 = everyone (when USE_FREEFORM_RENDER is on);
    # the rest fall back to catalog/IR. Lets ops ramp 10→50→100 via .env.
    freeform_traffic_pct: int = Field(default=100)

    # ── Art-Director → Writer 2-pass (owner directive 2026-06-01) ─────────
    # The FIXED build orchestration: a strong model (role `art_director`,
    # Opus) writes an ULTRA-DETAILED design brief (the reasoning + per-section
    # spec — NOT code), then a cheap model (role `freeform_writer`, DeepSeek)
    # writes the whole HTML by EXECUTING that brief. The expensive tokens buy
    # a compact senior-grade brief; the bulk HTML tokens run on the cheap
    # model — high design quality at low spend. Runs for every orchestrated
    # build regardless of model tier (no plain/catalog downgrade). Kill switch
    # for instant rollback to the prior single-shot freeform path (R-10).
    use_art_director_freeform: bool = Field(default=True)

    # Extend the SAME Art-Director → Writer 2-pass to container-backed APP
    # stacks that have a dedicated .tsx writer variant (currently
    # `nextjs_entities`, see art_director_writer._APP_TEMPLATES). Without this,
    # entity/app builds fall through to a bare single-shot .tsx pass — no design
    # brief, no authoritative theme tokens (hardcoded colours leak), no
    # `omnia:brief` event (the live narration / swatches stay blank). On = the
    # flagship enterprise apps get the same art-direction the freeform landings
    # already get. Kill switch for instant rollback to the single-shot path.
    use_art_director_entities: bool = Field(default=True)

    # Brief-lean Art-Director prompt (infra cost — 2026-06-16). The 2-pass build
    # sends the SAME ~14K-token system prompt to BOTH passes, but pass 1 (the
    # Art-Director) only writes a PROSE brief — it never emits code, so the
    # heaviest blocks (the shadcn app kit _ENTITIES_UI ~650 lines, the landing
    # section kit ~340, stack contracts, the <file> response format, the
    # self-check) are dead weight on its input. On = pass 1 gets a trimmed system
    # (design-thinking blocks only); pass 2 (the writer) keeps the FULL prompt, so
    # final code quality is unchanged. Kill switch for instant rollback to the
    # shared-full-prompt behaviour.
    use_lean_art_director_prompt: bool = Field(default=True)

    # ── Surgical edit mode (owner directive 2026-06-06) ───────────────────
    # After the first build, a follow-up that changes ONE thing (a selected
    # element, a recolour, a text swap, "add an intro section") is routed by the
    # triage to a single cheap model that emits a SURGICAL <edit> patch and is
    # forbidden — by a lean edit-only prompt AND by skipping the full-build
    # guards (palette/contrast/signature-floor/acceptance) — from touching the
    # rest of the page. Fixes the owner's two complaints: a small edit no longer
    # spins up the Art-Director→Writer premium pipeline (cost), and no longer
    # re-rolls the palette / regenerates other sections ("всё потерялось").
    # When False, edits fall back to the prior behaviour (full build prompt +
    # guards) for instant rollback (R-10). Kill per-env: USE_SURGICAL_EDIT=false.
    use_surgical_edit: bool = Field(default=True)

    # Container-app edit rewrite fallback (2026-06-21). When a surgical <edit>
    # can't land on a React/Next container app (no index.html → the static
    # rewrite fallbacks never fire), rewrite the TARGETED file(s) full-file via
    # the reliable writer model, guarded by a content-preservation ratio. Fixes
    # the owner's "сайт сломан → починить через чат не выходит, просто перестаёт
    # что-то делать": a failed <edit> on an entity/fullstack/spa app no longer
    # dead-ends. Kill per-env: USE_CONTAINER_EDIT_REWRITE=false.
    use_container_edit_rewrite: bool = Field(default=True)

    # ── App self-repair loop (Claude-Code «verify → fix», DARK) ───────────
    # After a container app (Next/entities/spa) hot-reloads generated files, the
    # dev server may fail to COMPILE or 5xx at RUNTIME. Today we only SURFACE that
    # as a chat card (`_probe_compile_errors`) and stop — the user is left with a
    # broken app and «добавь фичу» that didn't actually land. When > 0, the server
    # instead runs a BOUNDED self-repair loop: probe the real compile/runtime error
    # from the live container, feed it + the failing file to the `app_doctor` model
    # role (DeepSeek), hot-reload the fix, re-probe — up to N passes — then commit
    # the repaired files as a follow-up snapshot so the fix survives a rebuild. This
    # is the Claude-Code reliability step that lets DeepSeek actually ADD working
    # functionality, not just emit code that may not compile. Bounded + fail-soft
    # (R-10): any orchestrator/model hiccup falls back to surfacing the card (the
    # old behaviour). Default 0 = OFF (byte-identical to today). Recommended flip:
    # 2. Env: APP_SELF_REPAIR_PASSES.
    app_self_repair_passes: int = Field(default=0)

    # ── Feature scaffolding for container apps (give-it-functionality, DARK) ──
    # A follow-up like «добавь раздел бронирований / форму записи / каталог» on a
    # container app (Next/entities) routes to the surgical EDIT path (bias-to-edit,
    # by design). That path can create a page file, but it is NOT told the
    # entity-JSON contract or to WIRE the new route into the nav — so a data-backed
    # feature ships calling `entities.X.list()` with no `entities/X.json` (runtime
    # "unknown entity") or as an unreachable page ("добавил, но не видно"). When ON,
    # an "add functionality" edit on a container app gets a SCAFFOLD block in its
    # prompt: the exact entities/<Name>.json schema, the <CrudResource> route under
    # (app)/dashboard, and the mandatory nav-wiring edit — so DeepSeek creates a
    # COMPLETE, connected feature. Pairs with the self-repair loop (new files that
    # don't compile get auto-healed). Default OFF = byte-identical (no extra prompt
    # block). Recommended flip: true. Env: USE_FEATURE_SCAFFOLD.
    use_feature_scaffold: bool = Field(default=True)

    # Real-backend default (2026-06-27, owner: «мне ентитиз не нужны, нужен реальный
    # бэкенд»). When ON, a `web_app` result-type (accounts + saved data) routes to
    # the REAL full-stack stack (`fullstack` → nextjs-postgres-drizzle: Next API
    # routes + Postgres + Drizzle + Auth — the agent writes real route handlers and
    # schema) INSTEAD of the managed-CRUD `nextjs_entities` abstraction the owner
    # calls a prototype. Entities stays available (discovery can still recommend it
    # explicitly). Default ON; flip USE_REAL_BACKEND_DEFAULT=0 to restore today's
    # entities routing. Consumed by `services.discovery.result_type_to_stack`.
    use_real_backend_default: bool = Field(default=True)

    # Per-project design MOOD (2026-06-27, owner «дизайн всегда одинаковый, сделай
    # уникально»). When ON, a container-app BUILD prompt carries a seeded design
    # mood (curated WCAG-vetted palette + font + density + heading personality from
    # services.design_dna.design_mood_directive) so the agent writes a DISTINCT
    # look per project instead of the baked dark zinc/indigo template. Steers what
    # the agent writes → works even for the hardcoded realtime template (where CSS
    # token injection is inert). Default ON; flip USE_DESIGN_MOOD=0 to revert.
    use_design_mood: bool = Field(default=True)

    # Locked-primitive CONTRACT card (2026-06-27, harness-hardening). On a realtime
    # build the seed used to tell the agent «read the fixed files and check the
    # signatures yourself» — a weak model skips the reads and HALLUCINATES names /
    # shapes / arity (live: `getChannels` vs `listUserChannels` → TS2305; own
    # `Channel` type vs `@/lib/db/schema` → TS2322; `useChannel()` no-arg → TS2554),
    # then loops on the type errors. When ON we instead HAND the agent the exact
    # `.d.ts`-style signatures of the locked primitives up front (deep-module: a
    # narrow, exact interface beats "go discover it"), killing those error classes
    # deterministically. Default ON; flip USE_PRIMITIVE_CONTRACT=0 to revert.
    use_primitive_contract: bool = Field(default=True)

    # Ship-green-on-abort (2026-06-27, harness-hardening). A loop-guard abort
    # (cycle / repeat / explore / budget) used to ALWAYS return done=False →
    # «Сборка прервана», even when the last build was GREEN — so a compiling app
    # got discarded because the model fussed re-reading files after success (live:
    # messenger built green at step 15, then cycled on layout.tsx → thrown away).
    # When ON, any such abort with a clean last build ships as a success instead
    # (the `done` gate itself only requires a green build, so this meets the same
    # bar). Default ON; flip AGENT_SHIP_GREEN_ON_ABORT=0 to revert.
    agent_ship_green_on_abort: bool = Field(default=True)

    # Agentic builder (2026-06-22, Phase 0 of the "like Claude Code" engine).
    # When ON, container-app BUILDS (nextjs_entities/fullstack/spa, first build)
    # run through a real plan→act→observe→verify agent loop
    # (services/agent_builder.py): the model reads/writes files, runs a real
    # typecheck, sees the actual errors, and iterates until the build is clean —
    # instead of one-shot text→regex. Default OFF = byte-identical to today's
    # pipeline (the loop is never entered). Flip per-project for dogfood first.
    # Env: USE_AGENTIC_BUILDER. `agent_builder_max_steps` bounds the loop.
    use_agentic_builder: bool = Field(default=True)
    # Step budget for the loop. Explore-then-build on the entity template needs
    # headroom: the agent reads a couple of examples, declares N entities, writes
    # the screens, then build+fix. 14 was too tight (all spent exploring); 40
    # gives room to actually write + repair. Env: AGENT_BUILDER_MAX_STEPS.
    agent_builder_max_steps: int = Field(default=40)
    # Green-gate (Phase 2): when ON, the agent loop refuses a `done` until the
    # last build was clean AND the running app was re-checked after the last
    # write (a clean typecheck is exactly what a model hallucinates completion
    # around → a runtime-broken app shipped). Bounded internally so it nudges,
    # never hangs. Default OFF = today's behaviour. Env:
    # AGENT_REQUIRE_GREEN_BEFORE_DONE.
    agent_require_green_before_done: bool = Field(default=True)
    # Agentic-builder CANARY (2026-06-27): comma-separated user ids for whom the
    # agent loop runs even when use_agentic_builder is globally OFF. Lets the
    # agent be dogfooded on prod for specific accounts WITHOUT changing generation
    # for everyone (the flag is global; there's no per-project canary). Empty =
    # nobody (today's behaviour). Env: AGENTIC_BUILDER_CANARY_USERS.
    agentic_builder_canary_users: str = Field(default="")
    # Auto-continue: a single run is capped at agent_builder_max_steps, but a full
    # first build often needs more than one segment. Rather than stop at that cap and
    # make the user keep clicking «Продолжить» against an arbitrary low limit, the
    # build handler runs up to this many SEGMENTS back-to-back (each re-reads the live
    # container it's been writing to) until the agent calls done OR a whole segment
    # makes NO new file progress (genuinely stuck). This is the real stop condition;
    # the segment count is just a runaway backstop (a truly unbounded loop is unsafe —
    # a model that never finishes would run forever). Env: AGENT_MAX_SEGMENTS.
    agent_max_segments: int = Field(default=6)

    # Native tool-use agent (2026-07-01, owner «как Claude Code, только на сервере»).
    # When ON, a container-app build runs through agent_native.run_native_build: ONE
    # model (opus-4-8) drives it end-to-end via NATIVE Anthropic tool-use through the
    # gateway /v1/messages passthrough, extended thinking PRESERVED across tool turns,
    # and the only gate is FACT-based (the `build` tool feeds real compiler errors back;
    # no taste/vision judges) — instead of the brittle text-<omnia:action> protocol
    # (agent_builder.run_agent_build) that stalled builds. Default OFF: the text
    # protocol stays the prod default until the native path is verified on real builds
    # and billing is wired. Env: USE_NATIVE_AGENT.
    use_native_agent: bool = Field(default=False)

    # Edit auto-repair (owner 2026-06-28: «надо чтобы он ПРЯМ ЧИНИЛ, а не выдавал
    # „Не удалось завершить правку — нажми Починить“»). When a point-EDIT doesn't
    # land cleanly — nothing written, a red typecheck, or a 5xx render — don't ask
    # the user to click «Починить»: re-run the agent on the STRONG model with a
    # forceful «apply the change NOW + here is the concrete error» prompt and
    # build-to-green, up to edit_auto_repair_attempts times. Bounded + fail-soft
    # (a repair-run exception just stops the loop, never breaks the build). Kill
    # per-env: USE_EDIT_AUTO_REPAIR=false.
    use_edit_auto_repair: bool = Field(default=True)
    edit_auto_repair_attempts: int = Field(default=4)

    # Auto-heal ON OPEN (owner 2026-07-16 — "зашёл → ошибки чинятся сами").
    # When a project is opened/started and its dev build is RED, kick off the same
    # edit-repair the «Починить» button runs — automatically, in the background, no
    # click. OFF by default: an unprompted agent run spends tokens, so it ships
    # behind a flag to enable + observe deliberately. Guarded by a per-project
    # Redis debounce (autoheal_debounce_seconds) so a refresh storm can't re-fire
    # it. Env: USE_AUTOHEAL_ON_OPEN=true.
    use_autoheal_on_open: bool = Field(default=False)
    autoheal_debounce_seconds: int = Field(default=600)

    # Gate-feedback self-heal (unleash-the-model layer C). The agent now MAY write
    # custom server logic (the backend ban is lifted) — so after it says done we
    # statically verify the one unsafe thing (raw-DB escape via the backend
    # guardrail) and, when this flag is on, feed any violation back as the next
    # turn so the loop FIXES it (bounded by agent_gate_max_attempts) before commit.
    # OFF by default → the guardrail runs advisory-only (logs, never re-loops), so
    # prod generation is unchanged. Env: USE_AGENT_GATE_FEEDBACK.
    use_agent_gate_feedback: bool = Field(default=True)
    agent_gate_max_attempts: int = Field(default=2)
    # Runtime gates in the loop (research finding: functional/role gates were
    # defined+tested but UNWIRED — only the static guardrail ran). When on, after
    # the agent says done we drive the live preview through the functional gate
    # (signup → live SSE delivery → outsider-403) and feed a red verdict back as a
    # BLOCKING outcome, so a broken/leaky feature self-heals before the snapshot.
    # Realtime stacks only (functional gate is self-contained); fail-soft. ON by
    # default (owner 2026-06-28: a realtime build must PROVE two users can chat +
    # an outsider is denied 403 BEFORE it ships «зелёным» — a clean typecheck is
    # exactly what a weak model hallucinates «done» around, so realtime apps were
    # shipping unusable-but-green). Heal is discarded unless it stays typecheck-
    # green, so the gate can never ship WORSE than gate-off. Kill per-env:
    # USE_RUNTIME_GATES=false.
    use_runtime_gates: bool = Field(default=True)

    # Honest chat content (2026-06-21). The assistant message saved to the DB is
    # the model's RAW output (<file>/<edit> blocks + any stray prose/code). The
    # frontend renders anything NOT wrapped in a recognised block as raw text, so
    # a cheap model replying conversationally (```html / bare HTML) dumps CODE
    # into the chat ("выпуливает код, а не формирует документ"), and an <edit>
    # that didn't actually apply still renders a "Правка" chip ("писал правка, но
    # ничего не менялось"). When on, the saved content is rewritten to reflect
    # what ACTUALLY happened: loose code stripped, and a chip kept ONLY for files
    # that were really committed. Kill per-env: USE_CLEAN_CHAT_CONTENT=false.
    use_clean_chat_content: bool = Field(default=True)

    # Hero background visibility (owner directive 2026-06-06) — on every fresh
    # BUILD, guarantee the main screen shows its photo/graphic background instead
    # of a flat dark wash. The writer often buries the hero's full-bleed image
    # under a /70-/90 black overlay; this post-process deterministically lightens
    # that overlay + dims the WebGL shader so the on-theme image/graphic is
    # actually seen. Kill per-env: USE_HERO_BG_VISIBLE=false.
    use_hero_bg_visible: bool = Field(default=True)

    # ── Testing escape hatch — remove ALL generation gating ───────────────
    # When true: every generation is treated as free (is_free=True), so the
    # api wallet-floor check is skipped AND the gateway debit is skipped
    # (metadata.free=true). Owner directive 2026-06-01: during design testing
    # neither the 3-free-gen limit nor the wallet balance may block a
    # generation. Flip UNLIMITED_GENERATIONS=false to restore normal billing.
    unlimited_generations: bool = Field(default=False)

    # ── Exe-build (Windows installer, Task 6) ─────────────────────────────
    # POST /api/projects/{id}/build-exe — packages a Python project into a
    # Windows .exe + NSIS Setup installer via the orchestrator's /build-exe
    # route. Off by default (the omnia-exe-builder image is an optional
    # sidecar; flip on once it is present in docker-compose). Kill switch:
    # USE_EXE_BUILD=false.
    use_exe_build: bool = Field(default=True)
    # Hard-limit on the Setup.exe size (MB) the worker will accept before
    # refusing to upload. Prevents a runaway PyInstaller from exhausting
    # MinIO quota. Env: EXE_BUILD_MAX_MB.
    exe_build_max_mb: int = Field(default=150)

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def role_models_map(self) -> dict[str, str]:
        """Parse `role_models` CSV into a {role: model_id} dict."""
        out: dict[str, str] = {}
        for pair in self.role_models.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                if k.strip() and v.strip():
                    out[k.strip()] = v.strip()
        return out

    @property
    def multipass_models_set(self) -> frozenset[str]:
        """Models EXPLICITLY listed in the env var (no defaults mixed in).

        Kept for diagnostics and tests that want to see the raw operator
        intent. Production callers should use `effective_multipass_models`
        which folds in the CHEAP_MODELS default.
        """
        return frozenset(
            m.strip() for m in self.multipass_models.split(",") if m.strip()
        )

    @property
    def effective_multipass_models(self) -> frozenset[str]:
        """Models actually routed through the multipass pipeline.

        Always at least CHEAP_MODELS unless an explicit kill switch is in
        play. That guarantees a first-time user picking Haiku or Nano gets
        the multipass enterprise output immediately — no env setup, no
        "iterative mode" toggle to remember. Adding a new model id to
        CHEAP_MODELS in MODEL_TIER_MAP is enough to opt it in everywhere.
        """
        raw = (self.multipass_models or "").strip().lower()
        if raw in {"off", "none", "disabled"}:
            return frozenset()
        return CHEAP_MODELS | self.multipass_models_set


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


# ──────────────────────────────────────────────────────────────────────────
# Phase F.1 — Per-model prompt-routing tier map.
#
# Decouples the model the user picked from how we assemble the prompt for
# it. Premium models (Sonnet/Opus/GPT-5) keep focus on a 14 K-token system
# prompt and get the full single-shot brief. Budget models (Haiku/Nano)
# routinely drop focus past ~5 K tokens and will be routed through the
# multi-pass decomposition pipeline (Phase B, lands in a follow-up). Balanced
# models (Mini/Yandex-Pro/Gigachat/Gemini-Flash) get a trimmed single-shot —
# full design anchor + truncated `_DESIGN_KIT` / `_DETAILS_KIT`.
#
# Adding a new model means one line here; the prompt assembler reads only
# the tier, never the model id. Routing branch lives in `routers/messages.py`.
# ──────────────────────────────────────────────────────────────────────────

MODEL_TIER_MAP: dict[str, str] = {
    # Premium — full single-shot prompt, no decomposition.
    "claude-opus-4-7":   "premium",
    "claude-opus-4-8":   "premium",
    "gemini-3.5-flash-high": "premium",  # orchestrator (art_director)
    "deepseek-v4-pro-thinking": "premium",  # orchestrator (owner 06-02)
    "deepseek-v4-pro": "premium",  # coder (non-thinking, owner 06-02)
    "kimi-k2.6-thinking": "premium",  # design-brain (Kimi K2.6, vision+taste, owner 06-03)
    "claude-opus-4-6":   "premium",
    "claude-sonnet-4-6": "premium",
    "gpt-5":             "premium",
    "gemini-2.5-pro":    "premium",
    # Balanced — single-shot with trimmed kit blocks.
    "gpt-5-mini":        "balanced",
    "yandexgpt-5":       "balanced",
    "gigachat-2-pro":    "balanced",
    "gemini-2.5-flash":  "balanced",
    "gpt-4.1":           "balanced",
    # Budget — destined for the multi-pass pipeline (Phase B).
    "claude-haiku-4-5":  "budget",
    "gpt-5-nano":        "budget",
    "deepseek-v4-flash-thinking": "budget",  # vsegpt cheap thinking model
}

# `budget` tier IS the cheap-model set; CHEAP_MODELS stays as the
# vocabulary the plan and downstream readers (routers/messages.py) use.
CHEAP_MODELS: frozenset[str] = frozenset(
    m for m, tier in MODEL_TIER_MAP.items() if tier == "budget"
)

# Unknown model ids (frontend selector adds a model before tier map
# refresh) resolve to `balanced` — safest middle. Crashing on an unknown
# id would block the user from generating at all.
DEFAULT_TIER = "balanced"


def tier_for_model(model_id: str | None) -> str:
    """Resolve a model id to its prompt-routing tier.

    `model_id` is the canonical id used by the LLM gateway (matches the
    keys of `MODEL_TIER_MAP`). Unknown ids and `None` return
    `DEFAULT_TIER`, so caller logic stays uniform.
    """
    if not model_id:
        return DEFAULT_TIER
    return MODEL_TIER_MAP.get(model_id, DEFAULT_TIER)


# Generation modes — the single switch that decides HOW a model produces a
# site. `routers/messages.py` and `services/prompt_builder.py` both read this
# so the prompt we build and the way we parse the answer never disagree.
#   "freeform" — premium tier writes full HTML directly (Phase 11). Skip the
#                IR-JSON parse; run the acceptance gate.
#   "catalog"  — premium tier emits PageIR JSON → deterministic Jinja render.
#   "plain"    — budget/balanced tier; the freeform system prompt + multipass.
GenerationMode = str  # Literal["freeform", "catalog", "plain"] — kept loose for callers


def _in_freeform_rollout(project_id: str | None, pct: int) -> bool:
    """Deterministic per-project rollout bucket for freeform (Sprint 5).

    Same project always lands in or out of the rollout (stable look across
    re-prompts). `pct>=100` → everyone; `pct<=0` → nobody; a missing
    project_id is never excluded (internal callers without a project).
    """
    if pct >= 100:
        return True
    if pct <= 0:
        return False
    if not project_id:
        return True
    bucket = int.from_bytes(
        hashlib.sha256(str(project_id).encode()).digest()[:4], "big"
    ) % 100
    return bucket < pct


def generation_mode(
    model_id: str | None, project_id: str | None = None
) -> GenerationMode:
    """Decide the generation mode for a routing model.

    Freeform wins over catalog for premium models when the flag is on AND the
    project is inside the rollout bucket (Sprint 5); otherwise catalog/IR.
    Budget/balanced models always run "plain" (freeform-HTML + multipass) —
    catalog/IR never applies to them. Keeping this in ONE place means the
    prompt builder and the response parser can never drift apart (R-02).
    """
    settings = get_settings()
    if tier_for_model(model_id) == "premium":
        if settings.use_freeform_render and _in_freeform_rollout(
            project_id, settings.freeform_traffic_pct
        ):
            return "freeform"
        if settings.use_section_catalog:
            return "catalog"
    return "plain"


# ──────────────────────────────────────────────────────────────────────────
# Phase M — role→model orchestration map ("topmix-v1").
#
# The user no longer picks a model. Each task/pass in the generation pipeline
# has a ROLE, and each role is assigned the cheapest model that reliably does
# that job (Prompt Engineering for LLMs, ch. 9: "tasks don't have to all use
# the same LLM"). Hard structural reasoning → Opus; bulk Russian copy →
# DeepSeek; trivial mechanical steps → Haiku; final HTML assembly → no LLM
# (deterministic Jinja). Retune per-role at runtime via Settings.role_models
# (env ROLE_MODELS) without a code change.
# ──────────────────────────────────────────────────────────────────────────

ROLE_MODEL_MAP: dict[str, str] = {
    # Owner directive (2026-06-29): FULL SWITCH to Claude Opus 4.8 for EVERY LLM
    # role — DeepSeek / Kimi / Gemini dropped entirely. ONE model now runs the
    # whole orchestrator: classify → design (art_director + writer) → multipass →
    # agent loop → edit → self-heal → meta-calls (discovery / result_type). Served
    # by the vsegpt provider ("anthropic/claude-opus-4.8", same VSEGPT_API_KEY as
    # the old workers) and already a `premium` tier model (full single-shot prompt,
    # MODEL_TIER_MAP). Retune any single role at runtime via the ROLE_MODELS env
    # (e.g. agent=deepseek-v4-pro) with no code change; FORCE_MODEL pins one model
    # over the whole map.
    #
    # EXCEPTION — «кроме изображений» (owner): ONLY image GENERATION stays off Opus —
    # Settings.image_gen_model (flux) + the gpt-image path are untouched (not part of
    # this map). EVERYTHING else, including the screenshot/VISION judge, is Opus 4.8:
    # Opus is natively multimodal and the vsegpt provider now forwards image_url blocks
    # to it (`_is_vision` treats claude-opus-4-8 as multimodal), so the screenshot
    # actually reaches the judge.
    "classify":        "claude-opus-4-8",  # pick 1 of N presets
    "director":        "claude-opus-4-8",  # catalog orchestrator — structure
    "polish":          "claude-opus-4-8",  # writes the real PageIR content (RU copy)
    # VISION judge (screenshots) — Opus 4.8 (multimodal); also the `see`-tool judge.
    "audit":           "claude-opus-4-8",  # acceptance-gate screenshot judge
    "audit_retry":     "claude-opus-4-8",  # escalation re-roll judge
    "skeleton":        "claude-opus-4-8",  # multipass fallback — structure
    "content":         "claude-opus-4-8",  # multipass fallback — copy
    "visual":          "claude-opus-4-8",  # multipass fallback — style tokens
    "link_repair":     "claude-opus-4-8",  # rewrite dead hrefs
    "image_prompt":    "claude-opus-4-8",  # writes TEXT prompt for image-gen (gen stays flux)
    "single_shot":     "claude-opus-4-8",  # non-catalog freeform fallback path
    # Art-Director -> Writer 2-pass: design-brain writes the brief, writer/developer
    # writes the HTML. Both on Opus 4.8 now (was Kimi brief + DeepSeek writer).
    "art_director":    "claude-opus-4-8",
    "freeform_writer": "claude-opus-4-8",
    "edit":            "claude-opus-4-8",  # targeted edit
    "edit_escalation": "claude-opus-4-8",  # edit retry escalation (byte-exact SEARCH)
    # Agentic builder loop (plan->act->observe->verify, strict <omnia:action>
    # protocol, writes real code over many steps). Opus 4.8 follows the action
    # protocol and reasons over real build/runtime errors. NOTE: vsegpt bills by
    # characters and rate-limits ~1 req/sec, so a long Opus loop is heavier than
    # the old DeepSeek base; accepted per the owner's full-switch directive,
    # retunable via ROLE_MODELS.
    "agent":           "claude-opus-4-8",
    "agent_escalation":"claude-opus-4-8",  # one-shot escalation on a stuck loop
    # Meta-calls (onboarding question planner / result-type classifier) — tiny
    # structured JSON calls inside the POST /prompt budget. On Opus 4.8 now.
    "discovery_plan":  "claude-opus-4-8",
    "result_type":     "claude-opus-4-8",
    # Build-plan author (2026-06-30 «эскиз перед стройкой») — one structured-JSON
    # pass that emits the feature spec (screens/entities/capabilities) the agent
    # builds against and the coverage gate verifies. Opus: it must reason about a
    # product's real screens + endpoints, not just paraphrase. Retune via ROLE_MODELS.
    "planner":         "claude-opus-4-8",
    "exe_doctor":      "claude-opus-4-8",  # self-heal failed PyInstaller/NSIS builds
    "app_doctor":      "claude-opus-4-8",  # app self-repair (verify->fix)
}

# Any role not in the map (or pointing at a later-retired model) resolves here.
# Owner 2026-06-29 full switch: the safe bottom is Opus 4.8 too (was deepseek-chat),
# served by vsegpt ("anthropic/claude-opus-4.8").
DEFAULT_ROLE_MODEL = "claude-opus-4-8"

# First-N free "wow-effect" generations per user before wallet billing starts.
# Counter lives on User.free_generations_used; the gate is in routers/messages.py
# and the wallet-skip is in the LLM gateway (metadata.free=true).
FREE_GENERATION_LIMIT = 3


def model_for_role(role: str, override: str | None = None) -> str:
    """Resolve a pipeline role to the model id that should execute it.

    Precedence: explicit ``override`` (admin force-model) > Settings.role_models
    (ops env override) > ROLE_MODEL_MAP (topmix-v1 default) > DEFAULT_ROLE_MODEL.
    """
    if override:
        return override
    env_map = get_settings().role_models_map
    if role in env_map:
        return env_map[role]
    return ROLE_MODEL_MAP.get(role, DEFAULT_ROLE_MODEL)

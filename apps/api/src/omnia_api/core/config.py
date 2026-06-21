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
    use_vision_audit: bool = Field(default=False)
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
    use_followup_appification: bool = Field(default=False)
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
    acceptance_gauntlet_render_gates: bool = Field(default=False)
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
    acceptance_gauntlet_reference_gate: bool = Field(default=False)
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
    acceptance_gauntlet_catalog_gate: bool = Field(default=False)
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
    acceptance_entity_composition_gate: bool = Field(default=False)

    # ── Phase 11 — Sprint 4 (anti-generic) + Sprint 5 (rollout) ───────────
    # Originality: fingerprint each accepted freeform page and penalise the
    # next one that comes out near-identical to a DIFFERENT project's page
    # (the "every AI site looks the same" failure). Default ON (anti-generic) —
    # only active for freeform pages; catalog pages are intentionally alike.
    # OFF (owner 2026-06-03): like vision it fingerprints the acceptance-capture
    # screenshot — unreliable when remote images haven't painted — and in
    # score-only mode only labels, never blocks. Dead weight; killed with vision.
    use_originality: bool = Field(default=False)
    # Hamming distance (0..64 over a 64-bit dHash) at/below which two pages are
    # "too similar". Lower = stricter. ~10 catches near-duplicates without
    # flagging merely same-vibe pages.
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
    use_exe_build: bool = Field(default=False)
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
    # Owner directive (2026-05-30): ONE strong orchestrator (Opus) decides
    # structure; every worker/developer role runs on DeepSeek. DeepSeek is
    # served by the direct vsegpt provider — proxyapi's DeepSeek surface 404s,
    # vsegpt is the only working route (apps/llm-gateway providers/vsegpt.py).
    "classify":     "deepseek-chat",  # pick 1 of N presets
    # catalog ORCHESTRATOR (dormant in freeform) — was Sonnet/proxyapi, now vsegpt
    "director":     "deepseek-v4-pro-thinking",
    "polish":       "deepseek-chat",  # writes the real PageIR content (RU copy)
    # proxyapi.ru FULLY RETIRED (owner 2026-06-02). The acceptance-gate VISION
    # judge now runs on Gemini 3 Flash Preview via vsegpt (DeepSeek has no vision
    # model). The vsegpt provider was taught to PASS image_url blocks for `vis-`
    # models, so the screenshot reaches the judge. Enable with USE_VISION_AUDIT=true.
    "audit":        "gemini-3-flash-vision",  # acceptance-gate screenshot judge
    "audit_retry":  "gemini-3-flash-vision",  # escalation re-roll judge
    "skeleton":     "deepseek-chat",  # multipass fallback — structure
    "content":      "deepseek-chat",  # multipass fallback — copy
    "visual":       "deepseek-chat",  # multipass fallback — style tokens
    "link_repair":  "deepseek-chat",  # rewrite dead hrefs
    "image_prompt": "deepseek-chat",  # short image-gen prompt
    "single_shot":  "deepseek-chat",  # non-catalog freeform fallback path
    # Art-Director → Writer 2-pass, ALL DeepSeek (owner 2026-06-02: «везде дипсик,
    # чтобы точно отрабатывал»). ORCHESTRATOR (design-brain → brief) =
    # deepseek-v4-pro-thinking (reasoning helps design, separate reasoning field →
    # clean content). DEVELOPER (writes the HTML) = deepseek-v4-pro NON-thinking
    # (no reasoning overhead → faster, clean HTML). Both 1M context, both vsegpt.
    # Swap at runtime via ROLE_MODELS env — no code change.
    # Design-brain → Kimi K2.6 (owner 2026-06-03): native multimodal + stronger
    # design taste than DeepSeek. NON-thinking variant (2026-06-07): the -thinking
    # one 502s as art_director — deep reasoning on the large brief prompt (≈40K-token
    # system + directive) blows the gateway 240s timeout → empty brief → generic
    # build (caught live in an E2E). Non-thinking returns the brief fast, same price,
    # keeps Kimi's taste. Kimi writes the brief; DeepSeek freeform_writer transcribes.
    # Swap without a deploy via ROLE_MODELS env (e.g. art_director=gemini-3.5-flash-high).
    "art_director": "kimi-k2.6",
    "freeform_writer": "deepseek-v4-pro",
    "edit":         "deepseek-chat",  # cheap-path targeted edit
    # Onboarding question planner (owner rule 13 #1). A small structured meta-call
    # (NOT generation), runs INSIDE the 30s POST /prompt budget, so it needs a FAST,
    # reliable model that emits strict JSON. Owner directive 2026-06-16: route via
    # vsegpt (proxyapi.ru removed).
    # 2026-06-19: gemini-3.5-flash-high was BROKEN here — live on prod it returned
    # non-JSON junk / ReadTimeout'd at 22s → the planner fell to the GENERIC batch
    # for EVERY web prompt (the «вопросы не в попад / шаблонные» the owner hit). The
    # "-high" reasoning variant burns the token budget thinking. deepseek-chat
    # returns a tailored, parseable batch in ~3.6s (proven). Swap via ROLE_MODELS env.
    "discovery_plan": "deepseek-chat",
    "exe_doctor":    "deepseek-chat",  # self-heal patch for failed PyInstaller/NSIS builds
}

# Any role not in the map (or pointing at a later-retired model) resolves here.
# vsegpt deepseek-chat is the safe bottom — proxyapi.ru is RETIRED (owner
# 2026-06-02), so Haiku-via-proxyapi can no longer be the fallback.
DEFAULT_ROLE_MODEL = "deepseek-chat"

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

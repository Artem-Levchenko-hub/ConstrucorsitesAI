from functools import lru_cache

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
    # Default OFF — opt-in until we have golden-comparison telemetry vs
    # the freeform-HTML path. Toggle in prod: USE_SECTION_CATALOG=true.
    use_section_catalog: bool = Field(default=False)

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

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

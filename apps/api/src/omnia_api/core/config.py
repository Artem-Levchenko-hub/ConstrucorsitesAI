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
    # Comma-separated list of model IDs routed through the 4-pass pipeline
    # (skeleton → content → visual → assembly) instead of single-shot.
    # Default empty = nobody — Phase B stays dark until explicitly enabled.
    # Recommended initial value once stable: "claude-haiku-4-5,gpt-5-nano".
    multipass_models: str = Field(default="")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def multipass_models_set(self) -> frozenset[str]:
        return frozenset(
            m.strip() for m in self.multipass_models.split(",") if m.strip()
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

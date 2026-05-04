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
    minio_public_url: str = Field(default="http://localhost:9000")

    jwt_secret: SecretStr
    jwt_algorithm: str = Field(default="HS256")
    jwt_ttl_days: int = Field(default=7)
    jwt_cookie_name: str = Field(default="omnia_session")
    jwt_cookie_secure: bool = Field(default=False)

    llm_gateway_url: str = Field(default="http://localhost:8001")
    mock_llm: bool = Field(default=True)

    cors_origins: str = Field(default="http://localhost:3000")

    initial_wallet_balance_rub: float = Field(default=100.0)

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

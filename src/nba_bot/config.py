from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Postgres — single source of truth. docker-compose.yml reads the same
    # POSTGRES_* vars from .env, so credentials live in exactly one place.
    postgres_user: str = "nba_bot"
    postgres_password: str = ""
    postgres_db: str = "nba_bot"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    anthropic_api_key: str = ""
    odds_api_key: str = ""
    voyage_api_key: str = ""

    analysis_model: str = "claude-sonnet-5"
    embedding_model: str = "voyage-3"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

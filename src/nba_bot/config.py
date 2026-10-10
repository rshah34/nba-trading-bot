from pathlib import Path

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Anchored to the repo root so commands work from any directory (e.g. dashboard/).
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8", extra="ignore",
    )

    # Postgres — single source of truth. docker-compose.yml reads the same
    # POSTGRES_* vars from .env, so credentials live in exactly one place.
    postgres_user: str = "nba_bot"
    postgres_password: str = ""
    postgres_db: str = "nba_bot"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    anthropic_api_key: str = ""
    odds_api_key: str = ""

    analysis_model: str = "claude-sonnet-5"
    # Reads the day's news into structured player signals (minutes limits, role
    # changes, absences). Extraction, not judgement, so the small model does it.
    news_model: str = "claude-haiku-5-5"

    # Player props. Each market costs one Odds API credit per game per snapshot.
    prop_markets: str = (
        "player_points,player_rebounds,player_assists,player_threes,"
        "player_points_rebounds_assists,player_points_rebounds,player_points_assists,"
        "player_rebounds_assists,player_steals,player_blocks,player_blocks_steals,"
        "player_turnovers"
    )
    # Stop buying prop snapshots once the Odds API quota falls to this many credits,
    # so game lines (the CLV record) never get starved.
    odds_quota_reserve: int = 50
    # Game-line markets per snapshot (one credit each). The moneyline alone is what
    # bets and CLV use; spreads/totals only add reporting.
    game_markets: str = "h2h,spreads,totals"
    # Day of the month the Odds API quota refills — used to pace spending.
    odds_quota_reset_day: int = 1

    # The most bets (game winners + props together) recorded and texted per day.
    max_daily_bets: int = 3

    # Phone number or Apple ID that gets the best-bets text. Empty = log only.
    notify_imessage_to: str = ""
    bankroll: float = 0.0              # optional: shows stakes in dollars in the digest

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()

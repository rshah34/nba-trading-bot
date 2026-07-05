from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    abbreviation: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)
    conference: Mapped[str | None] = mapped_column(String, nullable=True)
    division: Mapped[str | None] = mapped_column(String, nullable=True)


class Game(Base):
    __tablename__ = "games"

    game_id: Mapped[str] = mapped_column(String, primary_key=True)
    season: Mapped[str] = mapped_column(String, nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), nullable=False)
    home_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="scheduled")
    home_rest_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_rest_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_back_to_back_home: Mapped[bool] = mapped_column(Boolean, default=False)
    is_back_to_back_away: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TeamGameStats(Base):
    __tablename__ = "team_game_stats"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"), primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), primary_key=True)
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fg_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    fg3_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    ft_pct: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    rebounds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turnovers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plus_minus: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class PlayerGameStats(Base):
    __tablename__ = "player_game_stats"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"), primary_key=True)
    player_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_name: Mapped[str] = mapped_column(String, nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), nullable=False)
    minutes: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rebounds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    steals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blocks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    turnovers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fgm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fga: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fg3m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fg3a: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ftm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fta: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plus_minus: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class Injury(Base):
    __tablename__ = "injuries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.team_id"), nullable=False)
    player_name: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Odds(Base):
    __tablename__ = "odds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"), nullable=False)
    sportsbook: Mapped[str] = mapped_column(String, nullable=False)
    home_moneyline: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_moneyline: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spread_home: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    spread_home_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    spread_away_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_points: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    over_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    under_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    is_closing_line: Mapped[bool] = mapped_column(Boolean, default=False)


class NewsArticle(Base):
    __tablename__ = "news_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    team_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class NewsChunk(Base):
    __tablename__ = "news_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id", ondelete="CASCADE"), nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint("game_id", "model_version", "as_of", name="predictions_game_model_asof_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ForeignKey("games.game_id"), nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    # The instant this prediction was made; all context is filtered to <= as_of.
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    predicted_home_win_prob: Mapped[float] = mapped_column(Numeric, nullable=False)
    predicted_spread: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    market_home_win_prob: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    market_spread: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_used: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class PredictionEvaluation(Base):
    __tablename__ = "prediction_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("predictions.id", ondelete="CASCADE"), nullable=False
    )
    actual_home_win: Mapped[bool] = mapped_column(Boolean, nullable=False)
    brier_score: Mapped[float] = mapped_column(Numeric, nullable=False)
    log_loss: Mapped[float] = mapped_column(Numeric, nullable=False)
    edge_vs_close: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    correctly_picked_winner: Mapped[bool] = mapped_column(Boolean, nullable=False)
    beat_spread: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

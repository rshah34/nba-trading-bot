from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from nba_bot.config import settings

# Pool sized so the parallel backtest runner (per-thread sessions) isn't
# connection-starved; well under Postgres' default max_connections of 100.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_size=10, max_overflow=20)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

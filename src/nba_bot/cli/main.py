from pathlib import Path

import typer
from rich import print as rprint

from nba_bot.agents import data_agent
from nba_bot.db.engine import SessionLocal, engine

app = typer.Typer()


@app.command()
def init_db():
    """Apply the SQL schema to DATABASE_URL. No-op if tables already exist."""
    sql_path = Path(__file__).resolve().parents[3] / "db" / "migrations" / "001_init.sql"
    with engine.connect() as conn:
        conn.exec_driver_sql(sql_path.read_text())
        conn.commit()
    rprint(f"[green]Applied schema from {sql_path}[/green]")


@app.command()
def ingest():
    """Run the nightly Data Agent: teams, games, injuries, box scores."""
    with SessionLocal() as session:
        result = data_agent.run_nightly(session)
    rprint("[green]Data Agent run complete:[/green]", result)


@app.command()
def ingest_odds():
    """Capture a snapshot of current NBA odds (all books) and attach to games.

    Run periodically through the day (e.g. hourly) to build line-movement history.
    """
    with SessionLocal() as session:
        result = data_agent.sync_odds(session)
    rprint("[green]Odds snapshot captured:[/green]", result)


@app.command()
def mark_closing():
    """Flag the last pre-tipoff odds snapshot per (game, book) as the closing line."""
    with SessionLocal() as session:
        marked = data_agent.mark_closing_lines(session)
    rprint(f"[green]Marked {marked} closing lines[/green]")


if __name__ == "__main__":
    app()

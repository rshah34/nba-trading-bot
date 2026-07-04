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


if __name__ == "__main__":
    app()

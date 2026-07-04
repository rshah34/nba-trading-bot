from datetime import date
from pathlib import Path

import typer
from rich import print as rprint

from nba_bot.agents import data_agent
from nba_bot.db.engine import SessionLocal, engine
from nba_bot.rag import ingest as news_ingest
from nba_bot.rag import retrieval

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


@app.command()
def ingest_news():
    """Fetch NBA news (RSS), embed new articles with Voyage, store in news_chunks."""
    with SessionLocal() as session:
        result = news_ingest.sync_news(session)
    rprint("[green]News ingestion complete:[/green]", result)


@app.command()
def search_news(
    query: str = typer.Argument(..., help="Free-text query to embed and search."),
    teams: str = typer.Option("", help="Comma-separated team abbreviations, e.g. LAL,BOS."),
    lookback: int = typer.Option(7, help="Only consider articles from the last N days."),
    k: int = typer.Option(5, help="Number of chunks to return."),
):
    """Debug the RAG retrieval: embed a query and print the closest news chunks."""
    from nba_api.stats.static import teams as static_teams

    by_abbr = {t["abbreviation"]: t["id"] for t in static_teams.get_teams()}
    team_ids = [by_abbr[a.strip().upper()] for a in teams.split(",") if a.strip()]
    if not team_ids:
        team_ids = list(by_abbr.values())  # no filter -> all teams

    with SessionLocal() as session:
        hits = retrieval.retrieve_relevant_news(
            session, query=query, team_ids=team_ids, as_of=date.today(), lookback_days=lookback, k=k
        )
    if not hits:
        rprint("[yellow]No matching news chunks.[/yellow]")
        return
    for h in hits:
        rprint(f"[cyan]{h.distance:.3f}[/cyan] [bold]{h.title}[/bold] [dim]({h.source})[/dim]")
        rprint(f"       {h.chunk_text[:200]}")


if __name__ == "__main__":
    app()

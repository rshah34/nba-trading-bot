import logging
from datetime import date
from pathlib import Path

import typer
from rich import print as rprint

from nba_bot import pipeline
from nba_bot.agents import analysis_agent, data_agent, evaluation_agent
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
def predict(
    game_date: str = typer.Option("", help="Date to predict (YYYY-MM-DD). Defaults to today."),
):
    """Run the Analysis Agent on all scheduled games for a date → win prob + edge."""
    target = date.fromisoformat(game_date) if game_date else date.today()
    with SessionLocal() as session:
        preds = analysis_agent.run_predictions(session, target)
    if not preds:
        rprint(f"[yellow]No scheduled games found for {target}.[/yellow]")
        return
    for p in preds:
        edge = (p.context_used or {}).get("edge_vs_market")
        edge_str = f"edge {edge:+.1%}" if isinstance(edge, (int, float)) else "no market line"
        rprint(
            f"[bold]{p.game_id}[/bold]  home win [cyan]{float(p.predicted_home_win_prob):.1%}[/cyan] "
            f"(margin {float(p.predicted_spread):+.1f})  {edge_str}"
        )
        rprint(f"       [dim]{p.reasoning}[/dim]")


@app.command()
def daily_pregame():
    """Pre-game phase: ingest data/injuries/results → capture odds → predict today's games."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SessionLocal() as session:
        rprint(pipeline.run_pregame(session))


@app.command()
def daily_postgame():
    """Post-game phase: ingest final results/box scores → mark closing lines → evaluate."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SessionLocal() as session:
        rprint(pipeline.run_postgame(session))


@app.command()
def backtest(
    season: str = typer.Option("2025-26", help="Season to replay, e.g. 2025-26."),
    limit: int = typer.Option(30, help="Number of games in the slice."),
    model: str = typer.Option("claude-haiku-4-5", help="Claude model for predictions."),
    season_slice: str = typer.Option("start", "--slice", help="Part of season: start|mid|end."),
    tag: str = typer.Option("", "--tag", help="Label this run (e.g. v1/v2) so reports don't mix."),
    run: bool = typer.Option(False, "--run", help="Actually spend API credits (default: estimate only)."),
):
    """Replay a season's games through Analysis + Evaluation. Estimates cost first."""
    import anthropic

    from nba_bot.backtest import loader, runner
    from nba_bot.config import settings

    with SessionLocal() as session:
        rprint(f"[cyan]Loading {season}...[/cyan]")
        rprint("  ", loader.load_season(session, season))
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        games = runner._backtest_games(session, season, limit, season_slice)
        est = runner.estimate_cost(session, games, model, client)
        window = f"{games[0].game_date} → {games[-1].game_date}" if games else "—"
        rprint(
            f"[cyan]Estimate:[/cyan] {est['games']} games ([bold]{season_slice}[/bold] slice, {window}) "
            f"× ~{est.get('input_tokens_each', 0)} input tokens → ~[bold]${est['est_usd']}[/bold] on {model}"
        )
        if not run:
            rprint("[yellow]Dry run — re-run with --run to execute (spends credits).[/yellow]")
            return

        rprint(f"[cyan]Running {len(games)} predictions...[/cyan]")
        rep = runner.predict_and_evaluate(
            session, games, model, runner.model_version_for(season, model, season_slice, tag), client=client
        )
        rprint("\n[green bold]=== Backtest report ===[/green bold]")
        rprint(f"games: {rep['n']}  winner accuracy: {rep['winner_accuracy']}  "
               f"Brier: {rep['mean_brier']}  log-loss: {rep['mean_log_loss']}")
        rprint(f"actual home win rate: {rep['home_win_rate_actual']}  "
               f"confident picks: {rep['accuracy_confident_picks']}")
        rprint("[bold]calibration (predicted vs actual):[/bold]")
        for b in rep["calibration"]:
            rprint(f"  {b['bin']}  n={b['n']:>3}  predicted={b['avg_predicted']}  "
                   f"actual={b['actual_win_rate']}")


@app.command()
def evaluate():
    """Score newly-resolved predictions (Brier, log-loss, CLV) and print the track record."""
    with SessionLocal() as session:
        result = evaluation_agent.run_evaluation(session)
    rprint(f"[green]Evaluated {result['evaluated']} new prediction(s).[/green]")
    rprint(
        f"  track record over {result['n_evaluations']} eval(s): "
        f"Brier={result['mean_brier']} log-loss={result['mean_log_loss']} "
        f"winner-hit={result['winner_hit_rate']} CLV={result['mean_clv']}"
    )


@app.command()
def backfill_team_box(
    season: str = typer.Option("2025-26", help="Season to retrofit team box raw counts for."),
    limit: int = typer.Option(0, help="Max games this run (0 = all pending; resumable)."),
    sleep: float = typer.Option(0.5, help="Seconds between nba.com calls (rate-limit courtesy)."),
):
    """Retrofit the authoritative team box (raw counts + OREB/DREB, migration 004)
    onto a season's games that were ingested before it. One V3 fetch per game."""
    from nba_bot.backtest import loader

    with SessionLocal() as session:
        result = loader.backfill_team_box(session, season, limit=limit or None, sleep=sleep)
    rprint("[green]Team-box backfill:[/green]", result)


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


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Bind address."),
    port: int = typer.Option(8000, help="Port to listen on."),
    reload: bool = typer.Option(False, help="Auto-reload on code changes (dev only)."),
):
    """Serve the read-only JSON API (predictions, edges, backtest metrics)."""
    import uvicorn

    uvicorn.run("nba_bot.api.app:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()

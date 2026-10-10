import logging
from datetime import date
from pathlib import Path

import typer
from rich import print as rprint

from nba_bot import pipeline
from nba_bot.agents import analysis_agent, data_agent, evaluation_agent
from nba_bot.db.engine import SessionLocal, engine

app = typer.Typer()


@app.command()
def init_db():
    """Apply every migration in db/migrations, in order. Safe to re-run: each one
    only adds what is missing, so this also brings an existing database up to date."""
    migrations = sorted((Path(__file__).resolve().parents[3] / "db" / "migrations").glob("*.sql"))
    # The raw driver connection, with no parameters, so a "%" in a SQL comment isn't
    # read as a placeholder and a file can hold several statements.
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            for path in migrations:
                cur.execute(path.read_text())
                rprint(f"[green]applied[/green] {path.name}")
        raw.commit()
    finally:
        raw.close()


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
    """Fetch NBA news from the RSS feeds and store articles not seen before."""
    from nba_bot.agents import news_agent

    with SessionLocal() as session:
        result = news_agent.sync_news(session)
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
    """Pre-game phase: ingest data/injuries/results/news → capture odds → predict → record bets."""
    _run_phase("pregame", pipeline.run_pregame)


def _run_phase(name: str, fn) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with SessionLocal() as session:
        result = fn(session)
    entry = pipeline.append_run_log(name, result)
    rprint(result)
    rprint(f"[{'green' if entry['ok'] else 'yellow'}]run {'ok' if entry['ok'] else 'had failures'}:[/] {entry['steps']}")


@app.command()
def daily_props(
    window_hours: float = typer.Option(
        0, help="Only games tipping within this many hours (the early run). 0 = whole slate."),
):
    """Props phase: injuries + news → signals → prop odds → prop bets → text the best bets."""
    window = window_hours or None
    _run_phase("props", lambda session: pipeline.run_props(session, window_hours=window))


@app.command()
def daily_close():
    """Closing snapshot: game lines + the prop markets we hold bets in (for CLV)."""
    _run_phase("close", pipeline.run_close)


@app.command()
def daily_postgame():
    """Post-game phase: final results/box scores → mark closing → evaluate → settle → text results."""
    _run_phase("postgame", pipeline.run_postgame)


@app.command()
def backtest(
    season: str = typer.Option("2025-26", help="Season to replay, e.g. 2025-26."),
    limit: int = typer.Option(30, help="Number of games in the slice."),
    model: str = typer.Option("claude-haiku-4-5", help="Claude model for predictions."),
    season_slice: str = typer.Option("start", "--slice", help="Part of season: start|mid|end."),
    tag: str = typer.Option("", "--tag", help="Label this run (e.g. v1/v2) so reports don't mix."),
    concurrency: int = typer.Option(8, help="Parallel prediction workers (each its own DB session)."),
    run: bool = typer.Option(False, "--run", help="Actually spend API credits (default: estimate only)."),
):
    """Replay a season's games through Analysis + Evaluation. Estimates cost first."""
    import anthropic

    from nba_bot.backtest import loader, runner
    from nba_bot.config import settings

    with SessionLocal() as session:
        if not runner._backtest_games(session, season, 1, "start"):
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
            session, games, model, runner.model_version_for(season, model, season_slice, tag),
            client=client, concurrency=concurrency,
        )
        rprint("\n[green bold]=== Backtest report ===[/green bold]")
        if rep.get("failed"):
            rprint(f"[yellow]{rep['failed']} game(s) failed and were skipped.[/yellow]")
        rprint(f"games: {rep['n']}  winner accuracy: {rep['winner_accuracy']}  "
               f"Brier: {rep['mean_brier']}  log-loss: {rep['mean_log_loss']}")
        rprint(f"actual home win rate: {rep['home_win_rate_actual']}  "
               f"confident picks: {rep['accuracy_confident_picks']}")
        rprint("[bold]calibration (predicted vs actual):[/bold]")
        for b in rep["calibration"]:
            rprint(f"  {b['bin']}  n={b['n']:>3}  predicted={b['avg_predicted']}  "
                   f"actual={b['actual_win_rate']}")


@app.command()
def calibrate(
    model_version: str = typer.Option(..., help="Model version whose resolved predictions to fit on."),
    k: int = typer.Option(5, help="Cross-validation folds for the honest out-of-sample read."),
    save: bool = typer.Option(False, "--save", help="Persist the fitted params so live betting applies them."),
):
    """Fit Platt calibration on a model's resolved predictions and report the
    cross-validated (out-of-sample) Brier / log-loss improvement."""
    from sqlalchemy import select

    from nba_bot.agents import betting_agent
    from nba_bot.db.models import Game, Prediction
    from nba_bot.features import calibration

    with SessionLocal() as session:
        rows = session.execute(
            select(Prediction.predicted_home_win_prob, Game.home_score, Game.away_score)
            .join(Game, Prediction.game_id == Game.game_id)
            .where(
                Prediction.model_version == model_version,
                Game.status == "final",
                Game.home_score.is_not(None),
            )
        ).all()

    if len(rows) < 2 * k:
        rprint(f"[yellow]Only {len(rows)} resolved predictions for '{model_version}' — need more to fit.[/yellow]")
        return

    raw = [float(p) for p, _, _ in rows]
    outcomes = [1 if hs > as_ else 0 for _, hs, as_ in rows]

    cv = calibration.cross_val_metrics(raw, outcomes, k=k)
    params = calibration.fit_platt(raw, outcomes)  # final params on all data

    rprint(f"[bold]Calibration for[/bold] {model_version}  (n={cv['n']}, {k}-fold CV)")
    rprint(f"  Brier    raw {cv['raw_brier']} → calibrated [cyan]{cv['cal_brier']}[/cyan]")
    rprint(f"  log-loss raw {cv['raw_log_loss']} → calibrated [cyan]{cv['cal_log_loss']}[/cyan]")
    better = cv["cal_brier"] < cv["raw_brier"] and cv["cal_log_loss"] <= cv["raw_log_loss"]
    rprint(f"  fitted params: a={params.a:.3f} (a<1 shrinks toward 0.5), b={params.b:+.3f}")
    rprint("[green]Calibration helps out-of-sample.[/green]" if better
           else "[yellow]No out-of-sample gain — model is already ~calibrated on this data.[/yellow]")
    if save:
        with SessionLocal() as session:
            betting_agent.save_calibration(session, model_version, params)
        rprint(f"[green]Saved calibration for {model_version} — live betting will apply it.[/green]")


@app.command()
def bets(
    model_version: str = typer.Option("", help="Filter to one model version (default: all)."),
):
    """Paper-trade track record from settled bets (CLV is the north-star metric)."""
    from nba_bot.agents import betting_agent

    with SessionLocal() as session:
        rec = betting_agent.track_record(session, model_version or None)
    if not rec["n_bets"]:
        rprint("[yellow]No settled bets yet.[/yellow]")
        return
    rprint(f"[bold]Paper-trade record[/bold] ({rec['n_bets']} settled bets)")
    rprint(f"  avg CLV: [cyan]{rec['avg_clv']:+.4f}[/cyan] prob pts  "
           f"(beat the close {rec['clv_positive_rate']:.0%} of the time)  ← north star")
    rprint(f"  win rate: {rec['win_rate']:.1%}   ROI: {rec['roi']:+.1%}   "
           f"bankroll: {rec['final_bankroll']:.3f}× start")


@app.command()
def props_backtest(
    season: str = typer.Option("2025-26", help="Season to replay."),
    start: str = typer.Option("", help="First date scored (YYYY-MM-DD); earlier games are warm-up."),
    availability: str = typer.Option(
        "all", help="none | last_game | oracle | all. See backtest/props_backtest.py."),
):
    """Score the prop projection model on a full season. Free — no API calls."""
    from nba_bot.backtest import props_backtest as pb
    from nba_bot.features.props import MARKET_LABELS, load_props_data

    with SessionLocal() as session:
        data = load_props_data(session)
    first = date.fromisoformat(start) if start else None
    modes = ("none", "last_game", "oracle") if availability == "all" else (availability,)
    for mode in modes:
        stats = pb.run_backtest(data, season, availability=mode, start=first)
        n = sum(st.n for st in stats.values()) or 1
        rprint(f"\n[bold]availability = {mode}[/bold]  "
               f"pooled MAE {sum(st.abs_model for st in stats.values()) / n:.4f} "
               f"(last-10 avg {sum(st.abs_l10 for st in stats.values()) / n:.4f})  "
               f"Brier {sum(st.brier for st in stats.values()) / n:.4f} "
               f"(naive {sum(st.brier_naive for st in stats.values()) / n:.4f})")
        if mode != modes[-1]:
            continue
        for market, st in stats.items():
            r = st.summary()
            rprint(f"  {MARKET_LABELS[market]:12} n={r['n']:>6}  MAE {r['mae_model']:.3f} "
                   f"(L10 {r['mae_l10']:.3f}, season {r['mae_season']:.3f})  bias {r['bias']:+.3f}  "
                   f"φ {r['phi']}  Brier {r['brier']:.4f} vs {r['brier_naive']:.4f}")
        rprint("[bold]calibration — predicted P(over) vs. how often it went over:[/bold]")
        for row in pb.calibration(stats):
            rprint(f"  {row['bin']}  n={row['n']:>6}  predicted={row['predicted']:.3f}  "
                   f"actual={row['actual']:.3f}")
    rprint("[dim]Lines here are each player's trailing-10 average, not a sportsbook's — this "
           "measures projection quality, not edge. Edge is only measurable live, via CLV.[/dim]")


@app.command()
def picks(
    game_date: str = typer.Option("", help="Date to price (YYYY-MM-DD). Defaults to today."),
    dry_run: bool = typer.Option(True, "--dry-run/--record", help="Preview (default) or record the picks."),
    refresh_odds: bool = typer.Option(False, "--refresh-odds", help="Buy fresh game + prop odds first."),
):
    """Show today's picks: the few best bets across game winners and player props."""
    from nba_bot import digest as digest_mod
    from nba_bot import picks as picks_mod
    from nba_bot.agents import props_agent

    target = date.fromisoformat(game_date) if game_date else date.today()
    with SessionLocal() as session:
        if refresh_odds:
            rprint("[cyan]game odds:[/cyan]", data_agent.sync_odds(session))
            rprint("[cyan]prop odds:[/cyan]", props_agent.sync_prop_odds(session))
        result = picks_mod.make_picks(session, target, dry_run=dry_run)
    rprint(f"{result['game_candidates']} game and {result['prop_candidates']} prop bet(s) cleared "
           f"the edge threshold; {result['slots']} slot(s) open today; {len(result['picks'])} picked"
           + ("" if dry_run else f", {result['placed']} recorded") + ".")
    for c in result["picks"]:
        d = c["decision"]
        rprint(f"  [bold]{digest_mod.pick_label(c)}[/bold] @ {d.decimal_odds}  model {d.model_prob:.0%} "
               f"vs mkt {d.market_prob:.0%}  edge {d.edge:+.1%}  stake {d.stake_fraction:.2%}")
        for note in c.get("notes", []):
            rprint(f"       [dim]{note}[/dim]")


@app.command()
def props_record():
    """Paper record on player props: bets (CLV first) and the model vs. the market."""
    from nba_bot.agents import props_agent

    with SessionLocal() as session:
        rec = props_agent.track_record(session)
    if not rec["n_bets"] and "lines" not in rec and "guide" not in rec:
        rprint("[yellow]No settled prop bets or scored lines yet.[/yellow]")
        return
    if rec["n_bets"]:
        clv = (f"{rec['avg_clv'] * 100:+.2f} pts (beat the close {rec['clv_positive_rate']:.0%})"
               if rec["avg_clv"] is not None else "n/a")
        rprint(f"[bold]Prop bets[/bold]: {rec['wins']}-{rec['losses']}-{rec['pushes']}  "
               f"{rec['units']:+.1f}u  ROI {rec['roi']:+.1%}  avg CLV [cyan]{clv}[/cyan]")
    if "guide" in rec:
        g = rec["guide"]
        rprint(f"[bold]Single-stat guidance[/bold]: {g['hits']} of {g['calls']} calls came in "
               f"({g['hits'] / g['calls']:.0%}; the model's bar is {g['target']:.0%})")
    if "lines" in rec:
        ln = rec["lines"]
        rprint(f"[bold]Every priced line[/bold] (n={ln['n']}): Brier model {ln['brier_model']} | "
               f"market {ln['brier_market']} | 50/50 blend {ln['brier_blend']}")
        rprint("[dim]The blend beating the market is the evidence the model adds information.[/dim]")


@app.command()
def extract_signals():
    """Read new articles into structured player signals (minutes limits, role changes)."""
    from nba_bot.agents import news_agent

    with SessionLocal() as session:
        rprint("[green]News signals:[/green]", news_agent.extract_signals(session))


@app.command()
def digest(
    game_date: str = typer.Option("", help="Date (YYYY-MM-DD). Defaults to today."),
    results: bool = typer.Option(False, "--results", help="Show last night's results instead."),
    send: bool = typer.Option(False, "--send", help="Also text it."),
):
    """Print (and optionally text) the best-bets message, or last night's results."""
    from nba_bot import digest as digest_mod
    from nba_bot import notify

    target = date.fromisoformat(game_date) if game_date else date.today()
    with SessionLocal() as session:
        text = digest_mod.results(session) if results else digest_mod.best_bets(session, target)
    if text is None:
        rprint("[yellow]Nothing settled recently.[/yellow]")
        return
    print(text)
    if send:
        rprint("text:", notify.send(text, kind="results" if results else "digest"))


@app.command()
def costs(days: int = typer.Option(30, help="How many days back to total.")):
    """What the Claude API has actually cost, by purpose and by day."""
    from nba_bot import usage

    report = usage.summarize(days)
    if not report["groups"]:
        rprint("[yellow]No API calls recorded yet.[/yellow]")
        return
    rprint(f"[bold]Claude API spend, last {days} days[/bold]")
    for name, g in report["groups"].items():
        rprint(f"  {name:30} {g['calls']:>5} calls  {g['input_tokens']:>9,} in  "
               f"{g['output_tokens']:>9,} out  [cyan]${g['usd']:.2f}[/cyan]")
    for day, usd in list(report["by_day"].items())[-10:]:
        rprint(f"  [dim]{day}  ${usd:.2f}[/dim]")
    rprint(f"  total [bold]${report['total_usd']:.2f}[/bold]")


@app.command()
def notify_test():
    """Send a test text to the configured number."""
    from nba_bot import notify

    result = notify.send("NBA bot test: notifications are working.", kind="test")
    if result == "off":
        rprint("[yellow]NOTIFY_IMESSAGE_TO is not set in .env — nothing sent.[/yellow]")
    else:
        rprint("text:", result)


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
def backfill_box_scores(
    season: str = typer.Option("2025-26", help="Season to fetch box scores for."),
    limit: int = typer.Option(0, help="Max games this run (0 = all pending; resumable)."),
    sleep: float = typer.Option(0.5, help="Seconds between nba.com calls (rate-limit courtesy)."),
):
    """Fetch player + team box scores for a loaded season's games that lack them.

    Only needed when setting up a past season from scratch; the daily pipeline
    keeps the current season filled in."""
    from nba_bot.backtest import loader

    with SessionLocal() as session:
        result = loader.backfill_box_scores(session, season, limit=limit or None, sleep=sleep)
    rprint("[green]Box-score backfill:[/green]", result)


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

"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { BacktestReport, ModelInfo, PredictionSummary } from "@/lib/types";
import { CalibrationChart } from "@/components/CalibrationChart";
import { EmptyState } from "@/components/EmptyState";
import { MetricTile } from "@/components/MetricTile";
import { ModelSelector } from "@/components/ModelSelector";
import { PredictionsTable } from "@/components/PredictionsTable";
import { ThemeToggle } from "@/components/ThemeToggle";

const pct = (v: number | null | undefined) =>
  v == null ? "—" : `${(v * 100).toFixed(1)}%`;
const num = (v: number | null | undefined, digits = 3) =>
  v == null ? "—" : v.toFixed(digits);

export default function Home() {
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [selected, setSelected] = useState<string>("");
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [recent, setRecent] = useState<PredictionSummary[]>([]);
  const [upcoming, setUpcoming] = useState<PredictionSummary[]>([]);
  const [fatalError, setFatalError] = useState<string | null>(null);

  // Load the model list once; default to the most-predicted model.
  useEffect(() => {
    const ctrl = new AbortController();
    api
      .models(ctrl.signal)
      .then((ms) => {
        setModels(ms);
        if (ms.length > 0) setSelected(ms[0].model_version);
      })
      .catch((e) => {
        if (e.name !== "AbortError")
          setFatalError(e instanceof ApiError ? e.message : "Unexpected error loading models.");
      });
    return () => ctrl.abort();
  }, []);

  // Load metrics + predictions whenever the selected model changes.
  useEffect(() => {
    if (!selected) return;
    const ctrl = new AbortController();
    Promise.all([
      api.backtest(selected, ctrl.signal),
      api.predictions({ modelVersion: selected, limit: 25 }, ctrl.signal),
      api.predictions({ upcoming: true, limit: 10 }, ctrl.signal),
    ])
      .then(([rep, rec, up]) => {
        setReport(rep);
        setRecent(rec);
        setUpcoming(up);
      })
      .catch((e) => {
        if (e.name !== "AbortError")
          setFatalError(e instanceof ApiError ? e.message : "Unexpected error loading data.");
      });
    return () => ctrl.abort();
  }, [selected]);

  // Derived, so we never call setState inside an effect: we're loading while the
  // model list is still null, or while the loaded report predates the selection.
  const loading =
    !fatalError && (models === null || (selected !== "" && report?.model_version !== selected));
  const hasBacktest = report != null && report.n > 0;

  return (
    <div className="mx-auto max-w-5xl px-5 py-8 sm:px-8">
      <header className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-primary">NBA Trading Bot</h1>
          <p className="mt-1 max-w-xl text-sm text-secondary">
            An LLM agent estimates each game&apos;s win probability independently of the betting
            line, then it&apos;s scored against the market. Below is the model&apos;s backtested
            track record.
          </p>
        </div>
        <ThemeToggle />
      </header>

      {fatalError ? (
        <EmptyState
          icon="🔌"
          title="Can't reach the API"
          description={fatalError}
        />
      ) : (
        <>
          {models && models.length > 0 && (
            <div className="mb-6">
              <ModelSelector models={models} value={selected} onChange={setSelected} />
            </div>
          )}

          {loading && !report ? (
            <div className="py-20 text-center text-sm text-muted">Loading…</div>
          ) : !hasBacktest ? (
            <EmptyState
              icon="📊"
              title="No scored predictions yet"
              description="Once the model has made predictions on games that have finished, its calibration and accuracy will show up here."
            />
          ) : (
            <section className="space-y-6">
              <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
                <MetricTile label="Games scored" value={String(report!.n)} />
                <MetricTile
                  label="Winner accuracy"
                  value={pct(report!.winner_accuracy)}
                  hint="picked the eventual winner"
                />
                <MetricTile label="Mean Brier" value={num(report!.mean_brier, 3)} hint="lower is better" />
                <MetricTile
                  label="Mean log-loss"
                  value={num(report!.mean_log_loss, 3)}
                  hint="lower is better"
                />
              </div>

              <div className="rounded-xl border border-[var(--border)] bg-surface p-5">
                <h2 className="text-sm font-medium text-primary">Calibration</h2>
                <p className="mb-4 mt-0.5 text-xs text-secondary">
                  When the model says 70%, does the home team win ~70% of the time? Points on the
                  dashed line are perfectly calibrated.
                </p>
                <CalibrationChart bins={report!.calibration} />
                <div className="mt-4 flex flex-wrap gap-x-8 gap-y-2 border-t border-[var(--border)] pt-4 text-xs text-secondary">
                  {report!.accuracy_confident_picks && (
                    <span>
                      Confident picks:{" "}
                      <span className="tnum font-medium text-primary">
                        {pct(report!.accuracy_confident_picks.accuracy)}
                      </span>{" "}
                      <span className="text-muted">({report!.accuracy_confident_picks.n})</span>
                    </span>
                  )}
                  {report!.accuracy_home_back_to_back && (
                    <span>
                      Home back-to-backs:{" "}
                      <span className="tnum font-medium text-primary">
                        {pct(report!.accuracy_home_back_to_back.accuracy)}
                      </span>{" "}
                      <span className="text-muted">({report!.accuracy_home_back_to_back.n})</span>
                    </span>
                  )}
                  <span>
                    Actual home win rate:{" "}
                    <span className="tnum font-medium text-primary">
                      {pct(report!.home_win_rate_actual)}
                    </span>
                  </span>
                </div>
              </div>
            </section>
          )}

          <section className="mt-10">
            <h2 className="mb-3 text-sm font-medium text-primary">Upcoming games</h2>
            {upcoming.length > 0 ? (
              <PredictionsTable rows={upcoming} />
            ) : (
              <EmptyState
                icon="🏀"
                title="No upcoming games"
                description="The season hasn't tipped off yet. Once games are scheduled and the live pipeline runs, predictions and edges will appear here."
              />
            )}
          </section>

          {hasBacktest && recent.length > 0 && (
            <section className="mt-10">
              <h2 className="mb-3 text-sm font-medium text-primary">
                Recent predictions <span className="text-muted">({selected})</span>
              </h2>
              <PredictionsTable rows={recent} />
            </section>
          )}

          <footer className="mt-12 border-t border-[var(--border)] pt-4 text-xs text-muted">
            Data served from <span className="tnum">{api.base}</span>
          </footer>
        </>
      )}
    </div>
  );
}

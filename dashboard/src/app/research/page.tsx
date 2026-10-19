"use client";

import { useEffect, useState } from "react";
import { ShowMore, usePaging } from "@/components/ShowMore";
import { Card, PageState, Segmented, SectionTitle, Tile } from "@/components/ui";
import { ApiError, post, useApi } from "@/lib/api";
import { STAT_LABELS, num, shortDate } from "@/lib/format";
import type { ResearchCandidate, ResearchFinding, ResearchPayload, ResearchRun, ResearchSide } from "@/lib/types";

const PAGE = 10;
const POLL_MS = 3000;

const VERDICT = {
  applied: { label: "Applied", pill: "border-accent/40 bg-accent/10", dot: "bg-accent" },
  pass: { label: "Passed", pill: "border-good/40 bg-good/10", dot: "bg-good" },
  fail: { label: "Failed", pill: "border-series-rest/60 bg-series-rest/15", dot: "bg-series-rest" },
};

const RUN_STATUS: Record<string, string> = {
  done: "Finished",
  out_of_budget: "Budget reached",
  out_of_turns: "Turn limit",
  refused: "Declined",
  error: "Error",
  running: "Running",
};

const CANDIDATE = { label: "Candidate", pill: "border-series-1/40 bg-series-1/10", dot: "bg-series-1" };

function Chips({ parts }: { parts: string[] }) {
  return (
    <>
      {parts.map((part) => (
        <span key={part} className="rounded-md border border-[var(--border)] px-1.5 py-0.5 text-xs text-secondary">
          {part}
        </span>
      ))}
    </>
  );
}

function Pill({ label, pill, dot }: { label: string; pill: string; dot: string }) {
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-1.5 py-0.5 text-[11px] font-medium text-primary ${pill}`}>
      <span className={`inline-block h-1.5 w-1.5 rounded-full ${dot}`} aria-hidden />
      {label}
    </span>
  );
}

const statLabel = (s: string) => STAT_LABELS[s as keyof typeof STAT_LABELS] ?? s;

/** "Too high by 0.41 ±0.20": bias is projection minus actual. */
function Bias({ side, stat }: { side: ResearchSide; stat: string }) {
  const half = (side.ci95[1] - side.ci95[0]) / 2;
  const digits = Math.abs(side.bias) < 0.1 ? 3 : 2;   // steals and blocks miss by hundredths
  return (
    <div>
      <div className="tnum text-sm font-semibold text-primary">
        {side.bias > 0 ? "Too high" : "Too low"} by {num(Math.abs(side.bias), digits)} {statLabel(stat)}
      </div>
      <div className="tnum text-xs text-muted">
        ±{num(half, digits)} · {side.rows.toLocaleString()} games, {side.players} players
      </div>
    </div>
  );
}

function FindingCard({ f }: { f: ResearchFinding }) {
  const verdict = f.applied_at ? VERDICT.applied : VERDICT[f.verdict];
  const better = f.mae[1] < f.mae[0];
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Pill {...verdict} />
        <Chips parts={f.slice} />
        <span className="tnum text-xs font-medium text-secondary">
          {statLabel(f.stat)} ×{f.multiplier}
        </span>
      </div>
      <div className="mt-2 text-sm font-medium text-primary">{f.hypothesis}</div>
      {f.rationale && <p className="mt-0.5 text-xs text-secondary">{f.rationale}</p>}
      <dl className="mt-3 grid gap-3 sm:grid-cols-3">
        <div>
          <dt className="text-xs text-muted">Explored on 2024-25</dt>
          <dd>
            <Bias side={f.explore} stat={f.stat} />
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Held back, 2025-26</dt>
          <dd>
            <Bias side={f.held} stat={f.stat} />
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Whole-model error, 2025-26</dt>
          <dd className="tnum text-sm font-semibold">
            <span className="text-primary">{num(f.mae[0], 4)}</span>
            <span className="text-muted"> → </span>
            <span className={better ? "text-good" : "text-critical"}>{num(f.mae[1], 4)}</span>
          </dd>
        </div>
      </dl>
      <details className="mt-3 text-xs text-secondary">
        <summary className="cursor-pointer font-medium text-accent">The checks</summary>
        <ul className="mt-1.5 space-y-0.5">
          {f.reasons.map((r) => (
            <li key={r} className="tnum">
              {r}
            </li>
          ))}
        </ul>
      </details>
    </Card>
  );
}

function CandidateCard({ c }: { c: ResearchCandidate }) {
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Pill {...CANDIDATE} />
        <Chips parts={c.slice} />
        <span className="tnum text-xs font-medium text-secondary">
          {statLabel(c.stat)} ×{c.multiplier}
        </span>
      </div>
      <div className="mt-2 text-sm font-medium text-primary">{c.hypothesis}</div>
      <p className="mt-0.5 text-xs text-secondary">{c.rationale}</p>
      <p className="tnum mt-1.5 text-xs text-muted">{c.evidence}</p>
    </Card>
  );
}

/** The run's steps: live while it works, folded away once it is done. */
function RunLog({ run }: { run: ResearchRun }) {
  if (run.log.length === 0) return null;
  const lines = (
    <ol className="tnum mt-1.5 space-y-0.5 font-mono text-[11px] text-secondary">
      {run.log.map((line, i) => (
        <li key={i}>{line}</li>
      ))}
    </ol>
  );
  if (run.status === "running") return <div className="mt-3 max-h-64 overflow-y-auto">{lines}</div>;
  return (
    <details className="mt-3">
      <summary className="cursor-pointer text-xs font-medium text-accent">Steps ({run.log.length})</summary>
      {lines}
    </details>
  );
}

function RunCard({ run }: { run: ResearchRun }) {
  const status = RUN_STATUS[run.status] ?? run.status;
  const running = run.status === "running";
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex items-center gap-2 text-sm font-semibold text-primary">
          {running && <span className="h-2 w-2 animate-pulse rounded-full bg-accent" aria-hidden />}
          {shortDate(run.started_at)} · {run.focus ?? "Open search"}
          <span className="text-xs font-normal text-muted">
            {run.mode === "explore" ? "explore only" : "explore and confirm"}
          </span>
        </div>
        <div className="tnum text-xs text-muted">
          {status} · {run.turns} turns · ${num(run.cost_usd, 2)}
          {run.budget_usd != null && ` of $${num(run.budget_usd, 2)}`}
        </div>
      </div>
      {run.summary && <p className="mt-1.5 text-sm text-secondary">{run.summary}</p>}
      {run.candidates.length > 0 && (
        <div className="mt-3 space-y-3">
          {run.candidates.map((c) => (
            <CandidateCard key={c.hypothesis} c={c} />
          ))}
        </div>
      )}
      {run.findings.length > 0 && (
        <div className="mt-3 space-y-3">
          {run.findings.map((f) => (
            <FindingCard key={f.id} f={f} />
          ))}
        </div>
      )}
      {run.explored.length > 0 && (
        <details className="mt-3 text-sm text-secondary">
          <summary className="cursor-pointer text-xs font-medium text-accent">
            {run.explored.length} more {run.explored.length === 1 ? "idea" : "ideas"} explored
          </summary>
          <ul className="mt-1.5 space-y-1">
            {run.explored.map((e) => (
              <li key={e.idea}>
                <span className="font-medium text-primary">{e.idea}</span> — {e.outcome}
              </li>
            ))}
          </ul>
        </details>
      )}
      <RunLog run={run} />
    </Card>
  );
}

function StartForm({ data, onStarted }: { data: ResearchPayload; onStarted: () => void }) {
  const { limits, held_back } = data;
  const [focus, setFocus] = useState("");
  const [budget, setBudget] = useState("2");
  const [mode, setMode] = useState<"explore" | "confirm">("explore");
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const busy = sending || limits.running;

  async function start() {
    setError(null);
    setSending(true);
    try {
      await post("/research/runs", { focus: focus.trim() || null, budget: Number(budget), mode });
      setFocus("");
      onStarted();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't start the run.");
    } finally {
      setSending(false);
    }
  }

  return (
    <Card className="mb-8 p-4">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex min-w-56 flex-1 flex-col gap-1 text-xs text-muted">
          Focus
          <input
            value={focus}
            onChange={(e) => setFocus(e.target.value)}
            placeholder="Anything, or e.g. rest and travel"
            maxLength={300}
            className="rounded-lg border border-[var(--border)] bg-surface px-2.5 py-1.5 text-sm text-primary placeholder:text-muted"
          />
        </label>
        <label className="flex w-24 flex-col gap-1 text-xs text-muted">
          Budget ($)
          <input
            type="number"
            min={0.5}
            max={limits.max_budget}
            step={0.5}
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
            className="tnum rounded-lg border border-[var(--border)] bg-surface px-2.5 py-1.5 text-sm text-primary"
          />
        </label>
        <Segmented
          label="Mode"
          value={mode}
          onChange={setMode}
          options={[
            { value: "explore", label: "Explore only" },
            { value: "confirm", label: "Explore and confirm" },
          ]}
        />
        <button
          type="button"
          onClick={start}
          disabled={busy}
          className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-plane transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          {limits.running ? "Running…" : sending ? "Starting…" : "Start run"}
        </button>
      </div>
      <p className="mt-2.5 text-xs text-secondary">
        {mode === "explore"
          ? "Explores 2024-25 and lists candidate fixes. The held-back season isn't touched."
          : `Can use up to ${limits.confirms_per_run} held-back confirmations, for good (${held_back.confirmations} used so far).`}{" "}
        <span className="tnum text-muted">
          ${num(limits.spent_today, 2)} of ${num(limits.daily_cap, 0)} spent today.
        </span>
      </p>
      {error && <p className="mt-2 text-xs font-medium text-critical">{error}</p>}
    </Card>
  );
}

export default function ResearchPage() {
  // Bumping `tick` refetches; while a run works, it ticks every few seconds.
  const [tick, setTick] = useState(0);
  const { data, error, loading } = useApi<ResearchPayload>(`/research?t=${tick}`);
  const paging = usePaging(PAGE);
  const running = data?.limits.running ?? false;
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setTick((t) => t + 1), POLL_MS);
    return () => clearInterval(id);
  }, [running]);
  if (!data) return <PageState loading={loading} error={error} />;
  const { runs, ready, held_back } = data;
  const tested = runs.reduce((n, r) => n + r.findings.length, 0);
  const passed = runs.reduce((n, r) => n + r.findings.filter((f) => f.verdict === "pass").length, 0);

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-primary">Research</h1>
      <p className="mt-1 max-w-2xl text-sm text-secondary">
        Where the agent thinks the projections go wrong, and whether it holds up on a season it never saw.
      </p>

      <div className="mt-6">
        <StartForm data={data} onStarted={() => setTick((t) => t + 1)} />
      </div>

      {runs.length > 0 && (
        <>
          <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Tile label="Runs" value={runs.length} />
            <Tile label="Fixes passed" value={`${passed} of ${tested}`} hint="taken to the held-back season" />
            <Tile label="Ready to apply" value={ready.length} />
            <Tile
              label={`Held back, ${held_back.season}`}
              value={`${held_back.confirmations} used`}
              hint={`the next fix must clear ${num(held_back.next_z, 2)} standard errors`}
            />
          </div>

          {ready.length > 0 && (
            <section className="mb-8">
              <SectionTitle>Ready to apply</SectionTitle>
              <div className="space-y-3">
                {ready.map((f) => (
                  <FindingCard key={f.id} f={f} />
                ))}
              </div>
            </section>
          )}

          <SectionTitle>Runs</SectionTitle>
          <div className="space-y-3">
            {runs.slice(0, paging.shown).map((r) => (
              <RunCard key={r.id} run={r} />
            ))}
          </div>
          <ShowMore total={runs.length} step={PAGE} {...paging} />
        </>
      )}
    </div>
  );
}

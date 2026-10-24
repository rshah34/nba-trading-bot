"use client";

import { useEffect, useState } from "react";
import { Card, PageState } from "@/components/ui";
import { ApiError, post, useApi } from "@/lib/api";
import { num, shortDate } from "@/lib/format";
import type { AskLimits, AskRecord } from "@/lib/types";

// Click to fill the box: the kinds of question the data can answer.
const EXAMPLES = [
  "How did the Celtics do after the All-Star break against top-10 teams last season?",
  "Who averaged the most assists in 2025-26?",
  "What does Jalen Brunson average in Knicks losses?",
  "Which teams had the best record on the second night of a back-to-back last season?",
  "Who is on the Lakers' injury report?",
];
const STATUS: Record<string, string> = {
  done: "",
  too_long: "Stopped: took too many steps",
  refused: "Declined",
  error: "Error",
  running: "Answering…",
};

/** **bold** inside a line, as React nodes (no HTML is injected). */
function inline(text: string) {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i} className="font-semibold text-primary">
        {part.slice(2, -2)}
      </strong>
    ) : (
      part
    ),
  );
}

/** The answer's paragraphs and "- " bullet lists. The model is asked for plain
 * sentences but reaches for a list when the answer is one. */
function AnswerText({ text }: { text: string }) {
  const blocks = text.split(/\n\s*\n/);
  return (
    <div className="mt-2 space-y-2 text-sm text-secondary">
      {blocks.map((block, i) => {
        const lines = block.split("\n").filter((l) => l.trim());
        if (lines.length > 0 && lines.every((l) => /^\s*[-*•] /.test(l)))
          return (
            <ul key={i} className="list-disc space-y-0.5 pl-5 marker:text-accent">
              {lines.map((l, j) => (
                <li key={j}>{inline(l.replace(/^\s*[-*•] /, ""))}</li>
              ))}
            </ul>
          );
        return <p key={i}>{inline(lines.join(" "))}</p>;
      })}
    </div>
  );
}

function AnswerCard({ a }: { a: AskRecord }) {
  const note = STATUS[a.status] ?? a.status;
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-sm font-semibold text-primary">{a.question}</div>
        <div className="tnum text-xs text-muted">
          {shortDate(a.asked_at)} · ${num(a.cost_usd, 3)}
        </div>
      </div>
      {note && <p className="mt-1 text-xs font-medium text-warning">{note}</p>}
      {a.answer && <AnswerText text={a.answer} />}
    </Card>
  );
}

export default function AskPage() {
  const [tick, setTick] = useState(0);
  const { data, error, loading } = useApi<AskLimits>(`/ask?t=${tick}`);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [latest, setLatest] = useState<AskRecord | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!asking) return;
    const started = Date.now();
    const id = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 500);
    return () => clearInterval(id);
  }, [asking]);

  async function submit() {
    if (!question.trim() || asking) return;
    setProblem(null);
    setElapsed(0);
    setAsking(true);
    try {
      setLatest(await post<AskRecord>("/ask", { question: question.trim() }));
      setQuestion("");
      setTick((t) => t + 1);
    } catch (e) {
      setProblem(e instanceof ApiError ? e.message : "Couldn't get an answer.");
    } finally {
      setAsking(false);
    }
  }

  if (!data) return <PageState loading={loading} error={error} />;

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-primary">Ask</h1>
      <p className="mt-1 max-w-2xl text-sm text-secondary">
        Questions about games, teams and players, answered from this site&apos;s database.
      </p>

      <Card className="mb-8 mt-6 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={2}
            maxLength={500}
            placeholder="e.g. What did Shai Gilgeous-Alexander average against top-10 teams last season?"
            className="min-w-64 flex-1 resize-y rounded-lg border border-[var(--border)] bg-surface px-2.5 py-1.5 text-sm text-primary placeholder:text-muted"
          />
          <button
            type="button"
            onClick={submit}
            disabled={asking || !question.trim()}
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-plane transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            {asking ? `Looking it up… ${elapsed}s` : "Ask"}
          </button>
        </div>
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5 text-xs">
          <span className="text-muted">Try:</span>
          {EXAMPLES.map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => setQuestion(ex)}
              className="rounded-full border border-[var(--border)] px-2 py-0.5 text-secondary transition-colors hover:border-accent hover:text-primary"
            >
              {ex}
            </button>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted">
          Covers the 2022-23 through 2025-26 regular seasons, this season so far, schedules, the latest injury report
          and recent news. No playoffs, play-by-play, shot charts or lineups.{" "}
          <span className="tnum">${num(data.spent_today, 2)} of ${num(data.daily_cap, 0)} used today.</span>
        </p>
        {problem && <p className="mt-2 text-xs font-medium text-critical">{problem}</p>}
      </Card>

      {latest && <AnswerCard a={latest} />}
    </div>
  );
}

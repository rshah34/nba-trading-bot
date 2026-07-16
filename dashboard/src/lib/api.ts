// Thin client for the nba_bot FastAPI. Base URL is configurable so the same
// build can point at a local server or a deployed one.
import type { BacktestReport, ModelInfo, PredictionSummary } from "./types";

const BASE = (
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8010"
).replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${BASE}${path}`, { signal, cache: "no-store" });
  } catch {
    // Network-level failure: server down, CORS, DNS, etc.
    throw new ApiError(`Could not reach the API at ${BASE}. Is \`nba-bot serve\` running?`);
  }
  if (!resp.ok) {
    throw new ApiError(`API returned ${resp.status} for ${path}`, resp.status);
  }
  return (await resp.json()) as T;
}

export const api = {
  base: BASE,
  models: (signal?: AbortSignal) => get<ModelInfo[]>("/models", signal),
  backtest: (modelVersion?: string, signal?: AbortSignal) =>
    get<BacktestReport>(
      modelVersion ? `/backtest?model_version=${encodeURIComponent(modelVersion)}` : "/backtest",
      signal,
    ),
  predictions: (
    opts: { modelVersion?: string; upcoming?: boolean; limit?: number } = {},
    signal?: AbortSignal,
  ) => {
    const q = new URLSearchParams();
    if (opts.modelVersion) q.set("model_version", opts.modelVersion);
    if (opts.upcoming) q.set("upcoming", "true");
    if (opts.limit) q.set("limit", String(opts.limit));
    const qs = q.toString();
    return get<PredictionSummary[]>(`/predictions${qs ? `?${qs}` : ""}`, signal);
  },
};

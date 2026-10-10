"use client";

// Thin client for the nba_bot FastAPI. Base URL is configurable so the same
// build can point at a local server or a deployed one.
import { useEffect, useState } from "react";

export const API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000").replace(
  /\/$/,
  "",
);

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${path}`, { signal, cache: "no-store" });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    // Network-level failure: server down, CORS, DNS, etc.
    throw new ApiError(`Could not reach the API at ${API_BASE}. Is \`nba-bot serve\` running?`);
  }
  if (!resp.ok) {
    throw new ApiError(
      resp.status === 404 ? "Not found." : `API returned ${resp.status} for ${path}`,
      resp.status,
    );
  }
  return (await resp.json()) as T;
}

interface ApiState<T> {
  data: T | null;
  error: string | null;
  /** True while a request is in flight; `data` keeps the previous result meanwhile. */
  loading: boolean;
}

/** Fetch `path` and refetch when it changes. Pass null to skip. */
export function useApi<T>(path: string | null): ApiState<T> {
  const [state, setState] = useState<{ path: string | null; data: T | null; error: string | null }>({
    path: null,
    data: null,
    error: null,
  });

  useEffect(() => {
    if (path === null) return;
    const ctrl = new AbortController();
    get<T>(path, ctrl.signal)
      .then((data) => setState({ path, data, error: null }))
      .catch((e: Error) => {
        if (e.name !== "AbortError")
          setState((prev) => ({ path, data: prev.data, error: e.message }));
      });
    return () => ctrl.abort();
  }, [path]);

  // Derived rather than stored, so nothing is set synchronously inside the effect.
  return { data: state.data, error: state.error, loading: path !== null && state.path !== path };
}

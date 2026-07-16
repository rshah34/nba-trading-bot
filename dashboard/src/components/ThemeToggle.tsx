"use client";

import { useSyncExternalStore } from "react";

type Theme = "light" | "dark";
const EVENT = "themechange";

// The theme lives on <html data-theme>, set before hydration by the inline
// script in layout.tsx (so there's no flash). We read it via an external store
// rather than effect state — no setState-in-effect, no cascading renders.
function subscribe(callback: () => void) {
  window.addEventListener(EVENT, callback);
  return () => window.removeEventListener(EVENT, callback);
}

function getSnapshot(): Theme {
  return (document.documentElement.getAttribute("data-theme") as Theme | null) ?? "light";
}

function setTheme(theme: Theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem("theme", theme);
  } catch {
    // localStorage may be unavailable (private mode); the in-memory attr still applies.
  }
  window.dispatchEvent(new Event(EVENT));
}

export function ThemeToggle() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, () => "light" as Theme);
  const next: Theme = theme === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      aria-label={`Switch to ${next} mode`}
      className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--border)] bg-surface text-sm text-secondary transition-colors hover:text-primary"
    >
      {theme === "dark" ? "☀" : "☾"}
    </button>
  );
}

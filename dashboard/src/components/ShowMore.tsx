"use client";

import { useState } from "react";

/** How many of a long list to show, stepping up and down by `step`. */
export function usePaging(step: number) {
  const [shown, setShown] = useState(step);
  return {
    shown,
    more: () => setShown((n) => n + step),
    less: () => setShown((n) => Math.max(step, n - step)),
    reset: () => setShown(step),
  };
}

/** Quiet "Show more" / "Show less" links under a list. */
export function ShowMore({ total, step, shown, more, less }: { total: number; step: number; shown: number; more: () => void; less: () => void }) {
  if (total <= step) return null;
  const link = "text-xs font-medium text-secondary hover:text-primary";
  return (
    <div className="mt-2 flex gap-4 px-2.5">
      {total > shown && (
        <button type="button" onClick={more} className={link}>
          Show more
        </button>
      )}
      {shown > step && (
        <button type="button" onClick={less} className={link}>
          Show less
        </button>
      )}
    </div>
  );
}

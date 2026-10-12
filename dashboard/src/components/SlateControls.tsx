"use client";

import { useState, type ReactNode } from "react";
import { useApi } from "@/lib/api";
import { longDate, shortDate } from "@/lib/format";
import type { SlatePlayers } from "@/lib/types";
import { Select } from "./ui";

/** Every projected player on one upcoming date, and the date picker's state. */
export function useSlatePlayers() {
  const [date, setDate] = useState("");
  const api = useApi<SlatePlayers>(date ? `/slate/players?date=${date}` : "/slate/players");
  return { ...api, setDate };
}

/** Page heading for the slate pages: title, what the page is, and which day. */
export function SlateHeader({
  title,
  data,
  setDate,
  children,
}: {
  title: string;
  data: SlatePlayers;
  setDate: (d: string) => void;
  children: ReactNode;
}) {
  const preseason = data.games.length > 0 && data.games.every((g) => g.preseason);
  return (
    <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-primary">{title}</h1>
        <p className="mt-1 max-w-2xl text-sm text-secondary">{children}</p>
        {data.date && (
          <p className="mt-1 text-sm text-secondary">
            <span className="font-medium text-primary">{longDate(data.date)}</span> · {data.games.length} game
            {data.games.length === 1 ? "" : "s"}
            {preseason ? " · preseason" : ""}
          </p>
        )}
      </div>
      {data.dates.length > 1 && data.date && (
        <Select
          label="Date"
          value={data.date}
          onChange={setDate}
          options={data.dates.map((d) => ({ value: d, label: shortDate(d) }))}
        />
      )}
    </div>
  );
}

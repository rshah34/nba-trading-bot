"use client";

import { useMemo, useState, type ReactNode } from "react";
import { ShowMore, usePaging } from "./ShowMore";

export interface Column<T> {
  key: string;
  header: string;
  /** What the column sorts by. Omit for a column that can't be sorted. */
  sort?: (row: T) => number | string | null | undefined;
  render: (row: T) => ReactNode;
  align?: "left" | "right";
  title?: string;
}

interface Props<T> {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string | number;
  initialSort?: { key: string; dir: "asc" | "desc" };
  /** Rows shown before "Show all"; omit to show everything. */
  pageSize?: number;
  onRowClick?: (row: T) => void;
  /** Tighter columns, for a wide table in a narrow card. */
  dense?: boolean;
}

/** A dense table whose column headers sort it. Numbers sort high-to-low first. */
export function SortableTable<T>({ rows, columns, rowKey, initialSort, pageSize, onRowClick, dense }: Props<T>) {
  const pad = dense ? "px-1.5" : "px-2.5";
  const [sort, setSort] = useState(initialSort ?? null);
  const paging = usePaging(pageSize ?? Infinity);

  const sorted = useMemo(() => {
    const col = sort && columns.find((c) => c.key === sort.key);
    if (!sort || !col?.sort) return rows;
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const x = col.sort!(a);
      const y = col.sort!(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1; // blanks last, whichever way the column is sorted
      if (y == null) return -1;
      return (typeof x === "string" ? x.localeCompare(String(y)) : x - (y as number)) * dir;
    });
  }, [rows, columns, sort]);

  const visible = pageSize ? sorted.slice(0, paging.shown) : sorted;

  function toggle(col: Column<T>) {
    if (!col.sort) return;
    const firstDir = col.align === "right" ? "desc" : "asc";
    setSort((s) =>
      s?.key === col.key ? { key: col.key, dir: s.dir === "asc" ? "desc" : "asc" } : { key: col.key, dir: firstDir },
    );
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-[var(--border)] bg-[var(--gridline)]/40 text-[11px] uppercase tracking-wide text-muted">
              {columns.map((c) => {
                const active = sort?.key === c.key;
                return (
                  <th
                    key={c.key}
                    scope="col"
                    title={c.title}
                    aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                    className={`whitespace-nowrap ${pad} py-2 font-medium ${c.align === "right" ? "text-right" : "text-left"}`}
                  >
                    {c.sort ? (
                      <button
                        type="button"
                        onClick={() => toggle(c)}
                        className={`uppercase tracking-wide hover:text-primary ${active ? "text-accent" : ""}`}
                      >
                        {c.header}
                        <span className="ml-0.5 inline-block w-2 text-[10px]" aria-hidden>
                          {active ? (sort!.dir === "asc" ? "▲" : "▼") : ""}
                        </span>
                      </button>
                    ) : (
                      c.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr
                key={rowKey(row)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={`border-b border-[var(--border)] last:border-0 even:bg-[var(--gridline)]/25 ${
                  onRowClick ? "cursor-pointer hover:bg-accent/10" : ""
                }`}
              >
                {columns.map((c) => (
                  <td
                    key={c.key}
                    className={`tnum whitespace-nowrap ${pad} py-1.5 ${
                      c.align === "right" ? "text-right" : "text-left"
                    } ${sort?.key === c.key ? "font-medium text-primary" : "text-secondary"}`}
                  >
                    {c.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pageSize && <ShowMore total={sorted.length} step={pageSize} {...paging} />}
    </div>
  );
}

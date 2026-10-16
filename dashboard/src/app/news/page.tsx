"use client";

import { useState } from "react";
import { ReportList } from "@/components/Reports";
import { ShowMore, usePaging } from "@/components/ShowMore";
import { Card, Empty, PageState, Segmented, Select } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { Directory, Report } from "@/lib/types";

type Topic = "all" | "availability" | "role";
const AVAILABILITY = new Set(["out", "doubtful", "questionable", "returning"]);
const PAGE = 15;

export default function NewsPage() {
  const { data, error, loading } = useApi<Report[]>("/news");
  const [team, setTeam] = useState("");
  const [topic, setTopic] = useState<Topic>("all");
  const paging = usePaging(PAGE);

  const { data: directory } = useApi<Directory>("/directory");
  const reports = (data ?? []).filter(
    (r) =>
      (!team || r.team === team) &&
      (topic === "all" || (topic === "availability") === AVAILABILITY.has(r.kind)),
  );
  if (!data) return <PageState loading={loading} error={error} />;

  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight text-primary">News</h1>
      <p className="mt-1 max-w-2xl text-sm text-secondary">
        Read from eight feeds and merged; the newest, firmest reports come first.
      </p>
      <div className="my-4 flex flex-wrap items-center gap-2">
        <Segmented
          label="Topic"
          value={topic}
          onChange={(t) => {
            setTopic(t);
            paging.reset();
          }}
          options={[
            { value: "all", label: "Everything" },
            { value: "availability", label: "Availability" },
            { value: "role", label: "Minutes and role" },
          ]}
        />
        <Select
          label="Team"
          value={team}
          onChange={(t) => {
            setTeam(t);
            paging.reset();
          }}
          options={[{ value: "", label: "All teams" }, ...(directory?.teams ?? []).map((t) => ({ value: t.abbr, label: t.name }))]} />
        <span className="tnum ml-auto text-xs text-muted">
          {reports.length} reports
        </span>
      </div>
      {reports.length > 0 ? (
        <>
          <Card className="px-4 py-1">
            <ReportList reports={reports.slice(0, paging.shown)} />
          </Card>
          <ShowMore total={reports.length} step={PAGE} {...paging} />
        </>
      ) : (
        <Empty title={team ? "Nothing current on this team" : "No reports match"}>
          News is read each afternoon for the teams playing that night, and a report drops off once it is out of date.
        </Empty>
      )}
    </div>
  );
}

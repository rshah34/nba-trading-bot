"use client";

import { useMemo, useState } from "react";
import { ReportList } from "@/components/Reports";
import { Card, Empty, PageState, Segmented, Select } from "@/components/ui";
import { useApi } from "@/lib/api";
import type { Report } from "@/lib/types";

type Topic = "all" | "availability" | "role";
const AVAILABILITY = new Set(["out", "doubtful", "questionable", "returning"]);

export default function NewsPage() {
  const { data, error, loading } = useApi<Report[]>("/news");
  const [team, setTeam] = useState("");
  const [topic, setTopic] = useState<Topic>("all");

  const teams = useMemo(() => [...new Set((data ?? []).map((r) => r.team).filter(Boolean) as string[])].sort(), [data]);
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
        What the bot has read out of eight NBA feeds: who is out, limited, returning, or changing role. Reports of the
        same thing are merged, and the firmest and most widely reported come first.
      </p>
      <div className="my-4 flex flex-wrap items-center gap-2">
        <Segmented
          label="Topic"
          value={topic}
          onChange={setTopic}
          options={[
            { value: "all", label: "Everything" },
            { value: "availability", label: "Availability" },
            { value: "role", label: "Minutes and role" },
          ]}
        />
        <Select label="Team" value={team} onChange={setTeam} options={[{ value: "", label: "All teams" }, ...teams.map((t) => ({ value: t, label: t }))]} />
        <span className="tnum ml-auto text-xs text-muted">{reports.length} reports</span>
      </div>
      {reports.length > 0 ? (
        <Card className="px-4 py-1">
          <ReportList reports={reports} />
        </Card>
      ) : (
        <Empty title="No reports match">News is read each afternoon, just before the picks are made.</Empty>
      )}
    </div>
  );
}

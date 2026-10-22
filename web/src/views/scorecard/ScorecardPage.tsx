// Scorecard (R5 T16a; docs/08 view 13): which kinds of companies answer us, per industry.
// Numbers first: people, contacted, meetings and deals over all industries, each with
// "last week N" from the snapshot 7 days back. A total with an unknown part is "—" (D118:
// never 0, never a made-up sum). Then the table, 10 industries a page, with the 7-day delta.
// Hooks: `#sc-table tbody tr`, `[data-stat]`.
import { ErrorLine, EmptyLine, LoadingLine } from "@/components/saley/states";
import { Pager } from "@/components/saley/Pager";
import { Stat } from "@/components/saley/Stat";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { useApi } from "@/hooks/use-api";
import { useOffset } from "@/hooks/use-kept";
import { num } from "@/lib/format";
import { pageOf, sumKnown } from "@/lib/team-rules";
import { fmt, W } from "@/words";
import { ScoreTable, type ScoreLastWeek, type ScoreRow } from "./ScoreTable";

// The server's own cap of one list (queries.MAX_LIMIT); the industries are far fewer.
const ALL_ROWS = 1000;

type ScorecardData = { items: ScoreRow[]; total: number; last_computed_label: string };
type Part = "people" | "contacted" | "meetings" | "deals";

const PARTS: { key: Part; label: string }[] = [
  { key: "people", label: W.colPeople },
  { key: "contacted", label: W.colContacted },
  { key: "meetings", label: W.colMeetings },
  { key: "deals", label: W.colDeals },
];

/** "last week N" for one total, or "—" when one industry has no snapshot for that week. */
function lastWeekLine(rows: ScoreRow[], key: keyof ScoreLastWeek): string {
  const last = sumKnown(rows.map((r) => (r.last_week ? r.last_week[key] : null)));
  return last === null ? "—" : fmt(W.packLastWeek, { v: last });
}

export function ScorecardPage({ view }: { view: ViewDef }) {
  const { data, error, loading, reload } = useApi<ScorecardData>(
    `/api/scorecard?limit=${ALL_ROWS}`,
  );
  const [offset, setOffset] = useOffset("scorecard");
  const rows = data?.items ?? [];
  const shown = pageOf(rows, offset);

  return (
    <ViewPage view={view}>
      {error && <ErrorLine what={W.whatScorecard} error={error} onRetry={reload} />}
      {!data && loading && <LoadingLine />}
      {data && (
        <div data-content className="flex flex-col gap-5">
          <div id="tiles" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {PARTS.map((p) => (
              <Stat
                key={p.key}
                id={p.key}
                label={p.label}
                value={rows.length ? sumKnown(rows.map((r) => r[p.key])) : null}
                detail={rows.length ? lastWeekLine(rows, p.key) : undefined}
              />
            ))}
          </div>
          <section className="bg-card min-w-0 rounded-lg border" aria-labelledby="sc-title">
            <header className="flex flex-wrap items-baseline justify-between gap-2 border-b px-4 py-3">
              <h2 id="sc-title" className="text-base font-semibold">
                {W.navScorecard}
              </h2>
              <span className="text-muted-foreground nums text-xs">
                {fmt(W.scIndustries, { n: num(data.total) })}
              </span>
            </header>
            <Pager
              total={rows.length}
              offset={offset}
              shown={shown.length}
              onMove={setOffset}
              label={W.navScorecard}
              className="pb-3 [&>nav]:px-4"
            >
              {rows.length === 0 ? (
                <div className="px-4 py-6">
                  <EmptyLine computed={data.last_computed_label} />
                </div>
              ) : (
                <ScoreTable id="sc-table" rows={shown} />
              )}
            </Pager>
          </section>
          <p className="text-muted-foreground nums text-xs">
            {fmt(W.lastComputed, { t: data.last_computed_label })}
          </p>
        </div>
      )}
    </ViewPage>
  );
}

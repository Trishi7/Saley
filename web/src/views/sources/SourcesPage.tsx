// Sources and health (R5 T16a; docs/08 view 14). Admin only: the rail shows it to the admin
// role of /api/me and the API refuses others (D150). Numbers first (jobs that failed, calls out
// today, LLM calls against the daily cap, news sources with a problem), then "Run news now",
// news today as a funnel, the jobs, calls out, the sheet tabs, the parity check, the data to
// fix and the connection lines. The news sources card (D225) shows what a funnel link picked.
// Hooks: `[data-stat]`, `#news-run`, `#jobs-table`.
import { ErrorLine, LoadingLine } from "@/components/saley/states";
import { Stat } from "@/components/saley/Stat";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { useApi } from "@/hooks/use-api";
import { sumKnown } from "@/lib/team-rules";
import { fmt, W } from "@/words";
import {
  Connections,
  DiscordTable,
  EgressTable,
  JobsTable,
  ParityTable,
  QualityTable,
  TabsTable,
} from "./HealthTables";
import { NewsBlock } from "./NewsBlock";
import { NewsRun } from "./NewsRun";
import { NewsSources } from "./NewsSources";
import type { HealthData } from "./types";

export function SourcesPage({ view }: { view: ViewDef }) {
  const { data, error, loading, reload } = useApi<HealthData>("/api/health/full");
  return (
    <ViewPage view={view}>
      {error && <ErrorLine what={W.whatSources} error={error} onRetry={reload} />}
      {!data && loading && <LoadingLine />}
      {data && (
        <div data-content className="flex flex-col gap-5">
          <div id="tiles" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat
              id="jobs-failed"
              label={W.jobsFailed}
              value={data.jobs.filter((j) => j.status === "failed").length}
            />
            <Stat
              id="calls-out"
              label={W.callsOut}
              value={data.egress.length ? sumKnown(data.egress.map((e) => e.calls)) : 0}
            />
            <Stat
              id="llm"
              label={W.llmToday}
              value={data.llm.calls}
              detail={fmt(W.llmOf, { c: data.llm_cap, k: data.llm.cached })}
            />
            <Stat
              id="sources-failed"
              label={W.newsFailedTitle}
              value={data.news.failed_sources.length}
            />
          </div>
          <NewsRun />
          <NewsBlock news={data.news} note={data.news_note} />
          <NewsSources news={data.news} />
          <JobsTable jobs={data.jobs} />
          <div className="grid gap-5 lg:grid-cols-2">
            <EgressTable egress={data.egress} />
            <TabsTable tabs={data.tabs} />
          </div>
          <ParityTable parity={data.parity} />
          <div className="grid gap-5 lg:grid-cols-2">
            <QualityTable data={data} />
            <Connections data={data} />
          </div>
          <DiscordTable rows={data.discord.rows} />
          <p className="text-muted-foreground nums text-xs">
            {fmt(W.lastComputed, { t: data.last_computed_label })}
          </p>
        </div>
      )}
    </ViewPage>
  );
}

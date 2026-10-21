// The news sources of the last 24 hours on Sources and health (docs/08 view 14, D73; the legacy
// `#news-sources-card`, ported at the cut-over, T16b D225). A news funnel number that counts
// sources or items per source ("Sources tried", "Stored as stories" …) links here with
// `#sources?news=<step>`: the card says which step it shows (`#news-pick`, "Showing: <step>"),
// marks the column that counts it and scrolls into view. Every value is the API's own text or
// a number. Hooks: `#news-sources`, `#news-pick`, `#src-table`, `th[data-hl]`.
import { useEffect, useRef } from "react";
import { OneLine } from "@/components/saley/Links";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useRoute } from "@/hooks/use-route";
import { num } from "@/lib/format";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";
import { Card, Paged } from "./HealthTables";
import type { HealthData } from "./types";

type Column = "runs" | "seen" | "new";

/** The column a funnel step counts (the legacy FUNNEL_PILL_COLUMN). */
const STEP_COLUMN: Record<string, Column> = {
  tried: "runs",
  answered: "runs",
  fetched: "seen",
  deduped: "new",
};

export function NewsSources({ news }: { news: HealthData["news"] }) {
  const picked = useRoute().params.news ?? "";
  const step = news.steps.find((s) => s.key === picked);
  const column = step ? STEP_COLUMN[step.key] : undefined;
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (step) box.current?.scrollIntoView({ block: "start" });
  }, [step]);
  const hl = (c: Column) => (c === column ? "bg-accent/60" : undefined);
  return (
    <div ref={box} id="news-sources" className="min-w-0 scroll-mt-4">
      <Card id="news-sources-card" title={W.newsSourcesTitle}>
        {step && (
          <p id="news-pick" className="text-muted-foreground border-b px-4 py-2 text-xs" role="status">
            {fmt(W.newsPickNote, { s: step.step })}
          </p>
        )}
        <Paged
          name="news-sources"
          items={news.sources}
          label={W.newsSourcesTitle}
          render={(rows) => (
            <Table id="src-table" className="min-w-[40rem] table-fixed">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="w-[26%]">{W.colSource}</TableHead>
                  <TableHead>{W.colLastAnswer}</TableHead>
                  <TableHead data-hl={column === "runs" || undefined} className={cn("w-[11%] text-right", hl("runs"))}>
                    {W.colTries}
                  </TableHead>
                  <TableHead className="w-[11%] text-right">{W.colErrors}</TableHead>
                  <TableHead data-hl={column === "seen" || undefined} className={cn("w-[13%] text-right", hl("seen"))}>
                    {W.colPulled}
                  </TableHead>
                  <TableHead data-hl={column === "new" || undefined} className={cn("w-[13%] text-right", hl("new"))}>
                    {W.colNewItems}
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((s) => (
                  <TableRow key={s.name} data-row data-source={s.name}>
                    <TableCell className="py-2 font-medium">
                      <OneLine text={s.active ? s.name : `${s.name} · ${W.sourceOff}`} />
                    </TableCell>
                    <TableCell className="text-muted-foreground py-2">
                      <OneLine text={s.last_status ?? W.sourceNever} />
                    </TableCell>
                    <TableCell className={cn("tabular py-2 text-right", hl("runs"))}>{num(s.runs_24h)}</TableCell>
                    <TableCell className="tabular py-2 text-right">{num(s.errors_24h)}</TableCell>
                    <TableCell className={cn("tabular py-2 text-right", hl("seen"))}>{num(s.seen_24h)}</TableCell>
                    <TableCell className={cn("tabular py-2 text-right", hl("new"))}>{num(s.new_24h)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        />
        {news.sources.length === 0 && <p className="empty text-muted-foreground px-4 py-6 text-sm">{W.noDataYet}</p>}
      </Card>
    </div>
  );
}

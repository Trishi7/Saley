// Numbers first (brief): the share of people contacted in each industry, as one bar per
// industry. Each row is a Button that filters the list to that industry (`industry:<name>`).
// One hue (the primary token) on a muted track: a single series needs no legend colour. An
// industry with no known share shows "—" and an empty track, never 0 %.
import { fmt, W } from "@/words";
import { cn } from "@/lib/utils";
import type { Industry } from "./types";

const SHOWN = 8; // the largest industries; the rest stay reachable through the search box

export function IndustryBars({
  industries,
  current,
  onPick,
}: {
  industries: Industry[];
  current: string;
  onPick: (filter: string) => void;
}) {
  const rows = [...industries].sort((a, b) => b.people - a.people).slice(0, SHOWN);
  if (rows.length === 0) return null;
  return (
    <section aria-labelledby="industry-title" className="bg-card rounded-lg border p-4" data-industries>
      <h2 id="industry-title" className="mb-3 text-sm font-semibold">
        {W.industryTitle}
      </h2>
      <ul className="grid grid-cols-1 gap-x-8 gap-y-1 md:grid-cols-2">
        {rows.map((row) => {
          const filter = `industry:${row.industry}`;
          const pct = row.pct === null ? "—" : `${row.pct}%`;
          return (
            <li key={row.industry}>
              <button
                type="button"
                aria-pressed={current === filter}
                aria-label={fmt(W.industryPick, { i: row.industry })}
                title={fmt(W.industryRow, { i: row.industry, n: row.people, pct })}
                onClick={() => onPick(filter)}
                className={cn(
                  "hover:bg-accent/60 focus-visible:ring-ring/50 grid w-full grid-cols-[minmax(0,9rem)_1fr_3rem] items-center gap-3 rounded-md px-2 py-1.5 text-left text-sm outline-none max-sm:min-h-11 focus-visible:ring-[3px]",
                  current === filter && "bg-accent",
                )}
              >
                <span className="truncate">{row.industry}</span>
                <span className="bg-muted h-2 overflow-hidden rounded-full">
                  <span
                    className="bg-primary block h-full rounded-full"
                    style={{ width: `${Math.min(100, Math.max(0, row.pct ?? 0))}%` }}
                  />
                </span>
                <span className="text-muted-foreground nums text-right text-xs">
                  {pct}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

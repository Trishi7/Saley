// Today, part 1: the numbers (brief: numbers first, as tiles or charts).
// - "Where we stand" is the funnel (D170, cumulative); each bar is an `a[data-tile]` that opens
//   the list it counts.
// - The tiles (`#tiles a.tile[data-tile] .v`) that the funnel does not already show. "People in a
//   live conversation" is the funnel's "Talking now" bar, and "Late items that block goals" is
//   the blockers line, so neither is a tile twice (proposed D202).
// - The blockers line sits INSIDE the tile area: one line with the count and "Open blockers".
import { ArrowRightIcon, CircleAlertIcon, CircleCheckIcon } from "lucide-react";
import { Funnel } from "@/components/saley/Funnel";
import { Tile } from "@/components/saley/Tile";
import { Button } from "@/components/ui/button";
import { hrefFor, num } from "@/lib/format";
import { cn } from "@/lib/utils";
import { W } from "@/words";
import type { TodayData } from "./types";

/** The tiles Today shows, in this order. The funnel and the blockers line hold the others. */
export const TILE_KEYS = ["deals", "backlog", "p1", "week"] as const;

export function Numbers({ data }: { data: TodayData }) {
  const tiles = TILE_KEYS.map((key) => data.tiles.find((t) => t.key === key)).filter(
    (t) => t !== undefined,
  );
  return (
    <section aria-labelledby="numbers-title" className="flex flex-col gap-4">
      <h2 id="numbers-title" className="text-base font-semibold">
        {W.funnelLabel}
      </h2>
      {data.funnel.length > 0 && (
        <div id="funnel" className="bg-card rounded-lg border p-3 sm:p-4">
          <Funnel steps={data.funnel} />
        </div>
      )}
      <div id="tiles" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {tiles.map((tile) => (
          <Tile key={tile.key} tile={tile} />
        ))}
        {data.blockers_line && <BlockersLine line={data.blockers_line} />}
      </div>
    </section>
  );
}

function BlockersLine({ line }: { line: NonNullable<TodayData["blockers_line"]> }) {
  const late = typeof line.count === "number" && line.count > 0;
  const Icon = late ? CircleAlertIcon : CircleCheckIcon;
  return (
    <div
      data-blockers-line
      data-count={num(line.count)}
      className={cn(
        "col-span-full flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-lg border px-4 py-3",
        late ? "bg-bad-soft border-transparent" : "bg-card",
      )}
    >
      <span className="flex min-w-0 items-center gap-2.5 text-sm">
        <Icon
          aria-hidden
          data-tag-ref={late ? "late" : undefined}
          className={cn("size-4 shrink-0", late ? "text-bad" : "text-muted-foreground")}
        />
        <span className={cn("font-medium text-pretty", late && "text-bad")}>{line.label}</span>
      </span>
      <Button asChild variant="outline" size="sm">
        <a href={hrefFor(line.link)} data-blockers-open>
          {W.openBlockers}
          <ArrowRightIcon aria-hidden />
        </a>
      </Button>
    </div>
  );
}

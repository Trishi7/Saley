// Weekly pack (R4 D117, R5 T16a; docs/08 view 12): this week against last week. The page draws
// the stored body that the test-channel post also uses (D134). Numbers first (team numbers with
// "last week N"; "—" when no snapshot), then late items, packed reminders ("Showing n of
// total", D186), tasks per person and the Scorecard rows.
// Week picker: the pack key is a Monday (`#pack?week=2026-10-19`). "Week before" and "Week
// after" move to the neighbour packs the server names (`prev_week` / `next_week`), so a click
// never asks for a week with no pack (R5 T17, D229). "Newest pack" drops the week. A fresh
// server answers 200 with `empty`: the page shows a calm line, never a failed request. A typed
// Monday with no pack gets the server's own line ("No pack for that week."), with the picker.
import { CalendarClockIcon, ChevronLeftIcon, ChevronRightIcon } from "lucide-react";
import { ErrorLine, LoadingLine } from "@/components/saley/states";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { Button } from "@/components/ui/button";
import { useApi } from "@/hooks/use-api";
import { useKept } from "@/hooks/use-kept";
import { useRoute } from "@/hooks/use-route";
import { dayLabel } from "@/lib/format";
import { go } from "@/lib/route";
import { isMonday } from "@/lib/team-rules";
import { fmt, W } from "@/words";
import { LateBlock, RemindBlock, ScoreBlock, TasksBlock, TeamTiles } from "./PackParts";
import type { PackData } from "./types";

const NOT_FOUND = 404;

type Near = { prev: string | null; next: string | null };

function WeekPicker({ near, newest }: { near: Near; newest: boolean }) {
  const move = (to: string | null) => go("pack", to ? { week: to } : undefined);
  return (
    <nav aria-label={W.weekLabel} className="flex flex-wrap gap-2" data-week-picker>
      <Button type="button" size="sm" variant="outline" disabled={!near.prev} onClick={() => move(near.prev)}>
        <ChevronLeftIcon aria-hidden />
        {W.weekBefore}
      </Button>
      <Button type="button" size="sm" variant="outline" disabled={!near.next} onClick={() => move(near.next)}>
        {W.weekAfter}
        <ChevronRightIcon aria-hidden />
      </Button>
      <Button type="button" size="sm" variant="ghost" disabled={newest} onClick={() => move(null)}>
        {W.weekNewest}
      </Button>
    </nav>
  );
}

export function PackPage({ view }: { view: ViewDef }) {
  const asked = useRoute().params.week ?? "";
  const week = isMonday(asked) ? asked.slice(0, 10) : null;
  const { data, error, loading, reload } = useApi<PackData>(week ? `/api/pack?week=${week}` : "/api/pack");
  const kept = useKept(data);
  const missing = error?.status === NOT_FOUND;
  const shown = missing ? undefined : kept;
  const b = shown?.body ?? null;
  const near: Near = { prev: shown?.prev_week ?? null, next: shown?.next_week ?? null };
  // The newest pack has no pack after it. A typed missing week can always go back to the newest.
  const newest = shown ? !shown.next_week : false;

  return (
    <ViewPage view={view}>
      {error && !missing && <ErrorLine what={W.whatPack} error={error} onRetry={reload} />}
      {!shown && !missing && loading && <LoadingLine />}
      {shown?.empty && (
        <p
          data-content
          data-pack-empty
          className="empty text-muted-foreground bg-card flex items-center gap-2 rounded-lg border px-4 py-6 text-sm"
        >
          <CalendarClockIcon aria-hidden className="size-4 shrink-0" />
          {W.packNone}
        </p>
      )}
      {(b || missing) && (
        <div data-content className="flex flex-col gap-5" aria-busy={loading || undefined}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p id="pack-week" className="text-muted-foreground nums text-sm">
              {b ? fmt(W.packWeekOf, { day: dayLabel(b.week_start), last: dayLabel(b.last_week_day) }) : dayLabel(week)}
            </p>
            <WeekPicker near={near} newest={newest} />
          </div>
          {missing && <p className="empty text-muted-foreground text-sm">{error?.message}</p>}
          {b && (
            <>
              <section className="flex flex-col gap-2" aria-labelledby="pack-team-t">
                <h2 id="pack-team-t" className="text-base font-semibold">
                  {W.packTeamTitle}{" "}
                  <span className="text-muted-foreground text-sm font-normal">{W.packTeamHint}</span>
                </h2>
                <TeamTiles team={b.team} />
              </section>
              <LateBlock body={b} />
              <RemindBlock body={b} />
              <TasksBlock body={b} />
              <ScoreBlock body={b} />
              <p className="text-muted-foreground nums text-xs">
                {fmt(W.lastComputed, { t: shown?.last_computed_label ?? "—" })}
              </p>
            </>
          )}
        </div>
      )}
    </ViewPage>
  );
}

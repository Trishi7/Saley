// Touches as a timeline strip (brief, Accounts and People: "one row per month, dots per touch
// type, hover detail, not a long table"). Data: `timeline[]` {month, label, touches: [{day,
// type, person}]} (plan §3.6). One focusable cell per day with touches (not one per dot), so
// the tab order stays short; hover or focus opens a tooltip with day, type and person.
// Touch types fold into four groups with the first three validated categorical colours plus
// a neutral (dataviz: never more than three hues where all pairs meet); the key row names them.
// Hooks: `[data-timeline]` (also on the empty line, with `data-empty`), `[data-month]` per row.
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { dayLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";
import { Swatch } from "./Tag";
import { toneColour, type Tone } from "./tones";

export type TimelineTouch = { day: string; type: string; person: string | null };
export type TimelineMonth = { month: string; label: string; touches: TimelineTouch[] };

type Group = "li" | "email" | "meet" | "other";
const DAYS_IN_STRIP = 31;
const DOTS_SHOWN = 3;

const GROUP_TONE: Record<Group, Tone> = {
  li: "touch-li",
  email: "touch-email",
  meet: "touch-meet",
  other: "touch-other",
};
const GROUP_NAME: Record<Group, string> = {
  li: W.groupLi,
  email: W.groupEmail,
  meet: W.groupMeet,
  other: W.groupOther,
};
const TYPE_NAME: Record<string, string> = {
  first_contact: W.touchFirst,
  li_req: W.touchLiReq,
  li_accept: W.touchLiAccept,
  li_dm: W.touchLiDm,
  email1: W.touchEmail1,
  email2: W.touchEmail2,
  email3: W.touchEmail3,
  meeting: W.touchMeeting,
  call: W.touchCall,
  reply: W.touchReply,
  bounce: W.touchBounce,
};

/** Pure: the colour group of a touch type. */
export function touchGroup(type: string): Group {
  if (type.startsWith("li_")) return "li";
  if (type.startsWith("email")) return "email";
  if (type === "meeting" || type === "call") return "meet";
  return "other";
}

/** The plain name of a touch type ("Email 2"); an unknown type keeps its own words. */
export function typeName(type: string): string {
  return TYPE_NAME[type] ?? type.replace(/_/g, " ");
}

/** Pure: the touches of one month by day of month (1–31). */
export function byDay(touches: TimelineTouch[]): Map<number, TimelineTouch[]> {
  const days = new Map<number, TimelineTouch[]>();
  for (const touch of touches) {
    const day = Number(touch.day.slice(8, 10));
    if (!day || day > DAYS_IN_STRIP) continue;
    days.set(day, [...(days.get(day) ?? []), touch]);
  }
  return days;
}

function line(touch: TimelineTouch): string {
  return fmt(touch.person ? W.touchLine : W.touchLineNoPerson, {
    day: dayLabel(touch.day),
    type: typeName(touch.type),
    person: touch.person ?? "",
  });
}

function DayCell({ day, touches }: { day: number; touches: TimelineTouch[] }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={touches.map(line).join("; ")}
          className="hover:bg-accent focus-visible:ring-ring/60 flex min-h-5 flex-col items-center justify-center gap-0.5 rounded-sm outline-none focus-visible:ring-2"
          style={{ gridColumn: day }}
        >
          {touches.slice(0, DOTS_SHOWN).map((touch, i) => (
            <span
              key={`${touch.type}-${i}`}
              aria-hidden
              data-touch={touchGroup(touch.type)}
              className="ring-card size-2 rounded-full ring-2"
              style={{ background: toneColour(GROUP_TONE[touchGroup(touch.type)]) }}
            />
          ))}
        </button>
      </TooltipTrigger>
      <TooltipContent>
        <ul className="flex flex-col gap-0.5">
          {touches.map((touch, i) => (
            <li key={`${touch.type}-${i}`}>{line(touch)}</li>
          ))}
        </ul>
      </TooltipContent>
    </Tooltip>
  );
}

export function TouchTimeline({
  timeline,
  className,
}: {
  timeline: TimelineMonth[] | null | undefined;
  className?: string;
}) {
  const months = timeline ?? [];
  if (!months.some((m) => m.touches.length > 0)) {
    return (
      <p data-timeline data-empty className="text-muted-foreground text-sm">
        {W.noTouches}
      </p>
    );
  }
  return (
    <figure data-timeline aria-label={W.timelineLabel} className={cn("flex flex-col gap-2", className)}>
      <div className="flex flex-col">
        {months.map((month) => (
          <div
            key={month.month}
            data-month={month.month}
            className="grid grid-cols-[4.5rem_1fr] items-center gap-2 border-b border-dashed py-1 last:border-b-0"
          >
            <span className="text-muted-foreground tabular text-xs">{month.label}</span>
            <div className="grid grid-cols-[repeat(31,minmax(0,1fr))] gap-px">
              {[...byDay(month.touches)].map(([day, touches]) => (
                <DayCell key={day} day={day} touches={touches} />
              ))}
            </div>
          </div>
        ))}
      </div>
      <figcaption className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {(Object.keys(GROUP_TONE) as Group[]).map((group) => (
          <span key={group} className="flex items-center gap-1.5">
            <Swatch tone={GROUP_TONE[group]} className="size-2" />
            {GROUP_NAME[group]}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

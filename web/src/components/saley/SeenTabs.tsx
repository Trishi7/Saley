// The "New" and "Seen earlier" tabs of Signals, Suggestions and Events (R5 T15, D157), each with
// its count from the last answer. Never a "N you saw are left out" line: the count is on the tab.
// Hooks: `[data-seen-tabs]`, `[data-seen-tab="new"|"seen"]` with `data-count` and
// `aria-pressed`, `[data-nothing-new]` for an empty New tab, `[data-seen-failed]` for the note
// shown when the seen mark failed twice (the New tab then pages by offset; T15 review).
import { InboxIcon, InfoIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { SeenTab } from "@/hooks/use-seen-list";
import { num } from "@/lib/format";
import { cn } from "@/lib/utils";
import { W } from "@/words";

const TABS: { id: SeenTab; label: string }[] = [
  { id: "new", label: W.tabNew },
  { id: "seen", label: W.tabSeen },
];

export function SeenTabs({
  tab,
  newTotal,
  seenTotal,
  onPick,
}: {
  tab: SeenTab;
  /** null while the first answer loads: the count shows "—", never 0. */
  newTotal: number | null;
  seenTotal: number | null;
  onPick: (tab: SeenTab) => void;
}) {
  return (
    <div
      role="group"
      aria-label={W.seenTabsLabel}
      data-seen-tabs
      className="bg-muted inline-flex w-fit rounded-lg p-[3px]"
    >
      {TABS.map(({ id, label }) => {
        const on = id === tab;
        const n = id === "new" ? newTotal : seenTotal;
        return (
          <Button
            key={id}
            type="button"
            size="sm"
            variant="ghost"
            aria-pressed={on}
            data-seen-tab={id}
            data-count={n ?? ""}
            onClick={() => onPick(id)}
            className={cn(
              "h-8 gap-2 rounded-md px-3 font-medium",
              on ? "bg-background text-foreground hover:bg-background shadow-sm" : "text-muted-foreground",
            )}
          >
            {label}
            <span
              className={cn(
                "tabular rounded-full px-1.5 text-xs",
                on && id === "new" && n ? "bg-primary text-primary-foreground" : "bg-background/70",
              )}
            >
              {num(n)}
            </span>
          </Button>
        );
      })}
    </div>
  );
}

/** The empty New tab (brief): one calm line, never a "left out" count. */
export function NothingNew() {
  return (
    <p
      data-nothing-new
      className="empty text-muted-foreground flex items-center gap-2 px-4 py-6 text-sm"
    >
      <InboxIcon aria-hidden className="size-4" />
      {W.nothingNew}
    </p>
  );
}

/** The seen mark failed twice: the New tab pages as a plain list for this visit. */
export function SeenMarkFailed() {
  return (
    <p role="status" data-seen-failed className="text-muted-foreground flex items-center gap-2 text-xs">
      <InfoIcon aria-hidden className="size-3.5" />
      {W.seenMarkFailed}
    </p>
  );
}

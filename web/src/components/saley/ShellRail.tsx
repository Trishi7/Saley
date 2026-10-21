// The left rail (docs/08 Shell): Run the plan · Watch · Team · Sources and health (admin only).
// Hook (Appendix B): `.rail a[data-v]`, each a link styled as a Button (never underlined).
// The same nav renders in the wide rail and in the phone menu (a Sheet), so both share hooks.
import { Button } from "@/components/ui/button";
import { isAdmin, useMe } from "@/hooks/use-me";
import { cn } from "@/lib/utils";
import { W } from "@/words";
import { RAIL_GROUPS, VIEWS } from "./views";

/** Blockers points here too while Money blocked is open (as the legacy rail). */
const ALSO_ON: Record<string, string> = { money: "blockers" };

export function RailNav({
  current,
  blockers,
  onPick,
  className,
}: {
  current: string;
  /** Late items count from /api/status; null hides the count. */
  blockers: number | null;
  onPick?: () => void;
  className?: string;
}) {
  const { me } = useMe();
  const admin = isAdmin(me);
  const active = ALSO_ON[current] ?? current;
  return (
    <nav aria-label={W.railLabel} className={cn("rail flex flex-col gap-5", className)}>
      {RAIL_GROUPS.filter((g) => g.group !== "admin" || admin).map((g) => (
        <div key={g.group} className="flex flex-col gap-0.5">
          <p className="text-muted-foreground px-2.5 pb-1 text-xs font-medium">{g.label}</p>
          {VIEWS.filter((v) => v.group === g.group).map((v) => {
            const on = v.id === active;
            const Icon = v.icon;
            return (
              <Button
                key={v.id}
                asChild
                variant="ghost"
                className={cn(
                  "text-foreground/80 h-8 justify-start gap-2.5 px-2.5 font-normal",
                  on && "bg-secondary text-secondary-foreground hover:bg-secondary font-medium",
                )}
              >
                <a
                  href={`#${v.id}`}
                  data-v={v.id}
                  aria-current={on ? "page" : undefined}
                  className={cn(on && "on")}
                  onClick={onPick}
                >
                  <Icon aria-hidden className={cn("size-4", on ? "text-primary" : "opacity-70")} />
                  <span className="truncate">{v.nav}</span>
                  {v.id === "blockers" && blockers !== null && blockers > 0 && (
                    <span
                      data-count="blockers"
                      className="tabular bg-bad-soft text-bad ml-auto rounded-full px-1.5 text-xs"
                    >
                      {blockers}
                    </span>
                  )}
                </a>
              </Button>
            );
          })}
        </div>
      ))}
    </nav>
  );
}

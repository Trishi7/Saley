// A number with its label, for the "numbers first" row of a page whose numbers are not links
// (R5 T15: Blockers, Money). Same look as a Tile; a Tile is a link, a Stat is not. A value that
// is not known shows "—", never 0. Hook: `[data-stat="<key>"] .v`.
import { num } from "@/lib/format";
import { cn } from "@/lib/utils";

export function Stat({
  id,
  label,
  value,
  detail,
}: {
  id: string;
  label: string;
  value: number | string | null;
  detail?: string;
}) {
  const unknown = value === null || value === "";
  return (
    <div data-stat={id} className="bg-card flex min-w-0 flex-col gap-1 rounded-lg border p-4">
      <span className="text-muted-foreground text-sm leading-snug">{label}</span>
      <span
        className={cn(
          "v tabular text-3xl font-semibold tracking-tight",
          unknown && "text-muted-foreground",
        )}
      >
        {num(value)}
      </span>
      {detail && <span className="text-muted-foreground text-xs">{detail}</span>}
    </div>
  );
}

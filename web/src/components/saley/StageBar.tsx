// A stacked bar of people by stage (brief, Accounts: "13 people · 9 P1" and a stage bar).
// Each stage wears its named colour (D201), the same token as the legend swatch, with a
// 2 px gap between segments. The bar is an image with a full text label, and each segment
// has a tooltip, so colour is never the only code. No people: a dash, never 0.
// Hooks (R5-A03): `[data-stagebar]` on the bar, `[data-stage][data-n]` on each segment.
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { share } from "@/lib/format";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";
import { stageRank, stageTone, toneColour } from "./tones";

/** Stages with people, in pipeline order. Pure: the browser check and the label share it. */
export function stageParts(counts: Record<string, number>): [string, number][] {
  return Object.entries(counts)
    .filter(([, n]) => n > 0)
    .sort(([a], [b]) => stageRank(a) - stageRank(b) || a.localeCompare(b));
}

export function StageBar({
  counts,
  className,
}: {
  counts: Record<string, number> | null | undefined;
  className?: string;
}) {
  const parts = stageParts(counts ?? {});
  const total = parts.reduce((sum, [, n]) => sum + n, 0);
  if (total === 0) {
    return (
      <span data-stagebar className="text-muted-foreground nums text-sm">
        —
      </span>
    );
  }
  const label = fmt(W.stageBarLabel, {
    parts: parts.map(([stage, n]) => `${stage} ${n}`).join(", "),
  });
  return (
    <div
      data-stagebar
      role="img"
      aria-label={label}
      className={cn("flex h-2.5 w-full min-w-16 gap-0.5 overflow-hidden rounded-full", className)}
    >
      {parts.map(([stage, n]) => (
        <Tooltip key={stage}>
          <TooltipTrigger asChild>
            <span
              data-stage={stage}
              data-n={n}
              className="h-full first:rounded-l-full last:rounded-r-full"
              style={{ width: `${share(n, total)}%`, background: toneColour(stageTone(stage)) }}
            />
          </TooltipTrigger>
          <TooltipContent>{fmt(W.stagePart, { stage, n })}</TooltipContent>
        </Tooltip>
      ))}
    </div>
  );
}

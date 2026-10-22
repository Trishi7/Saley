// Goal progress ring (D159, D171): won ÷ need. Without a need the centre shows "—", never 0.
// The percent can pass 100: the ring stops at full, the number shows the real value.
import { RingChart } from "@/components/charts/ring-chart";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";

export type Progress = { won: number; need: number | null; pct: number | null };

/** Pure: the centre text and the label. `pct` null (no need on file) gives the dash. */
export function ringText(progress: Progress): { center: string; label: string } {
  if (progress.need === null || progress.pct === null) {
    return { center: "—", label: fmt(W.ringNoNeed, { won: progress.won }) };
  }
  return {
    center: `${progress.pct}%`,
    label: fmt(W.ringLabel, { won: progress.won, need: progress.need }),
  };
}

export function Ring({ progress, className }: { progress: Progress; className?: string }) {
  const { center, label } = ringText(progress);
  return (
    <figure
      data-ring
      role="img"
      aria-label={label}
      className={cn("flex flex-col items-center gap-1", className)}
    >
      <RingChart value={progress.pct} label={label} center={center} className="size-24" />
      <figcaption aria-hidden className="text-muted-foreground nums text-xs">
        {label}
      </figcaption>
    </figure>
  );
}

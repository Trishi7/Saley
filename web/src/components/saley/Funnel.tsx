// The Today funnel (D201: each bar in its stage colour, the legend's token; brief: "Where we stand" as a funnel chart with the counts; plan §3.6):
// In the list -> Contacted -> Connected -> Talking now -> Met -> Won. The vendored bklit funnel
// draws the shape; on top, one real link per stage (`a[data-tile]`, `.v` value, Appendix B)
// covers its segment, so a click opens the list it counts and the keyboard reaches each stage.
// Hover or focus on a link highlights its segment. A stage the sheet never gave shows "—";
// when the first stage is unknown or zero there is no shape to draw, so only the links show.
// Text never sits on the coloured shape: labels and counts live in the band above and below
// it (wide) or beside it (narrow), so contrast is the card's text contrast.
import { useState } from "react";
import { FunnelChart } from "@/components/charts/funnel-chart";
import { useMedia } from "@/hooks/use-media";
import { hrefFor, num, type ViewLink } from "@/lib/format";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";
import { FUNNEL_TONES, toneColour } from "./tones";

/** One bar. `link` is a view of this app; `href` is a ready hash from the API (the news funnel
 *  of Sources and health, R5 T16a), used as it is, never built here. */
export type FunnelStep = {
  key: string;
  label: string;
  n: number | null;
  link?: ViewLink;
  href?: string;
};

const GAP_PX = 4; // the same gap the chart uses between segments

/** Pure: can the shape be drawn? It needs a first stage above zero. */
export function funnelDrawable(steps: FunnelStep[]): boolean {
  const first = steps[0]?.n;
  return typeof first === "number" && first > 0;
}

export function Funnel({
  steps,
  className,
  label = W.funnelLabel,
}: {
  steps: FunnelStep[];
  className?: string;
  /** The accessible name of the chart (from W). */
  label?: string;
}) {
  const wide = useMedia("(min-width: 640px)");
  const [hovered, setHovered] = useState<number | null>(null);
  const drawable = funnelDrawable(steps);
  // D201: each bar wears its stage colour, the same token as the legend swatch.
  const data = steps.map((s) => ({
    label: s.label,
    value: s.n ?? 0,
    color: toneColour(FUNNEL_TONES[s.key] ?? "primary"),
  }));
  return (
    <figure data-funnel aria-label={label} className={cn("relative", className)}>
      {drawable && (
        <div aria-hidden className={wide ? "pt-6 pb-9" : "px-[30%]"}>
          <FunnelChart
            data={data}
            orientation={wide ? "horizontal" : "vertical"}
            color="var(--chart-1)"
            layers={2}
            gap={GAP_PX}
            showLabels={false}
            showValues={false}
            showPercentage={false}
            hoveredIndex={hovered}
            onHoverChange={setHovered}
            staggerDelay={0.06}
            style={{ aspectRatio: wide ? "4.2 / 1" : "1 / 1.6" }}
          />
        </div>
      )}
      <div
        className={cn(
          "grid",
          drawable && "absolute inset-0 z-30",
          wide ? "grid-flow-col auto-cols-fr" : "grid-flow-row auto-rows-fr",
        )}
        style={{ gap: GAP_PX }}
      >
        {steps.map((step, i) => (
          <a
            key={step.key}
            href={step.href ?? (step.link ? hrefFor(step.link) : undefined)}
            data-tile={step.key}
            aria-label={fmt(W.funnelStep, { label: step.label, n: num(step.n) })}
            onMouseEnter={() => setHovered(i)}
            onMouseLeave={() => setHovered(null)}
            onFocus={() => setHovered(i)}
            onBlur={() => setHovered(null)}
            className={cn(
              "flex min-w-0 rounded-md p-2 outline-none",
              "focus-visible:ring-ring/60 focus-visible:ring-[3px]",
              wide ? "flex-col justify-between" : "items-center justify-between gap-3",
              !drawable && "bg-card hover:bg-accent/40 border",
            )}
          >
            <span className="text-muted-foreground flex items-center gap-1.5 text-xs leading-tight font-medium">
              <span
                aria-hidden
                data-tag-ref={FUNNEL_TONES[step.key] ? FUNNEL_TONES[step.key] : undefined}
                className="size-2 shrink-0 rounded-full"
                style={{ background: toneColour(FUNNEL_TONES[step.key] ?? "primary") }}
              />
              {step.label}
            </span>
            <span
              className={cn(
                "v tabular text-xl font-semibold",
                step.n === null && "text-muted-foreground",
              )}
            >
              {num(step.n)}
            </span>
          </a>
        ))}
      </div>
    </figure>
  );
}

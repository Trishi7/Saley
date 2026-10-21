// shadcn/ui chart (Recharts), vendored as source and changed for the CSP (D146, D148).
// Saley change: upstream `ChartStyle` writes the series colours into a <style> element with
// dangerouslySetInnerHTML. That element has no nonce, so style-src blocks it. Here the colours
// are CSS variables on the container's `style` prop. React sets them through CSSOM, which the
// CSP allows. Dark mode needs no second rule: the colours point at theme tokens (--chart-1 …),
// and the .dark class changes those tokens.
import type * as React from "react";
import * as RechartsPrimitive from "recharts";
import { cn } from "@/lib/utils";

export type ChartConfig = Record<string, { label?: string; color?: string }>;

function colourVars(config: ChartConfig): React.CSSProperties {
  const vars: Record<string, string> = {};
  for (const [key, item] of Object.entries(config)) {
    if (item.color) vars[`--color-${key}`] = item.color;
  }
  return vars as React.CSSProperties;
}

function ChartContainer({
  config,
  className,
  style,
  children,
  ...props
}: React.ComponentProps<"div"> & {
  config: ChartConfig;
  children: React.ComponentProps<typeof RechartsPrimitive.ResponsiveContainer>["children"];
}) {
  return (
    <div
      data-slot="chart"
      className={cn(
        "flex aspect-video justify-center text-xs [&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground [&_.recharts-cartesian-grid_line[stroke='#ccc']]:stroke-border/50 [&_.recharts-layer]:outline-hidden [&_.recharts-surface]:outline-hidden",
        className,
      )}
      style={{ ...colourVars(config), ...style }}
      {...props}
    >
      <RechartsPrimitive.ResponsiveContainer>{children}</RechartsPrimitive.ResponsiveContainer>
    </div>
  );
}

const ChartTooltip = RechartsPrimitive.Tooltip;

type TooltipItem = { name?: string | number; value?: string | number; color?: string };

/** A small tooltip body. Labels come from the chart config, which takes them from W. */
function ChartTooltipContent({
  active,
  payload,
  config,
}: {
  active?: boolean;
  payload?: TooltipItem[];
  config: ChartConfig;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-background grid min-w-32 gap-1 rounded-lg border px-2.5 py-1.5 text-xs shadow-xl">
      {payload.map((item) => {
        const key = String(item.name ?? "");
        return (
          <div key={key} className="flex items-center justify-between gap-3">
            <span className="text-muted-foreground">{config[key]?.label ?? key}</span>
            <span className="tabular text-foreground">{item.value}</span>
          </div>
        );
      })}
    </div>
  );
}

export { ChartContainer, ChartTooltip, ChartTooltipContent };

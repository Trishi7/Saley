// A tag on the page. `data-tag-ref` names its legend entry: the browser check
// `legend_every_page` (R5-Q01) finds each one in the page legend (`[data-legend] [data-tag]`).
import type { ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { BADGE, toneColour, type Tone } from "./tones";

export function Swatch({ tone, className }: { tone: Tone; className?: string }) {
  return (
    <span
      aria-hidden
      data-swatch
      className={cn("inline-block size-2.5 shrink-0 rounded-full", className)}
      style={{ background: toneColour(tone) }}
    />
  );
}

export function Tag({
  tag,
  tone,
  children,
  className,
}: {
  /** The legend key this tag refers to. */
  tag: string;
  tone: Tone;
  children: ReactNode;
  className?: string;
}) {
  const wash = BADGE[tone];
  return (
    <Badge
      variant="outline"
      data-tag-ref={tag}
      className={cn("rounded-full font-medium", wash ?? "text-foreground bg-card", className)}
    >
      {!wash && <Swatch tone={tone} className="size-2" />}
      {children}
    </Badge>
  );
}

// The legend at the top of every page (brief: "tags explained in a legend at the top of each
// page"; R5-Q01). One row of swatch + name + a plain sentence. Hooks: `[data-legend]` on the
// box, `[data-tag="<key>"]` on each entry; tags on the page carry `data-tag-ref="<key>"`.
import { W } from "@/words";
import { Swatch } from "./Tag";
import type { Tone } from "./tones";

export type LegendItem = { tag: string; tone: Tone; name: string; explain: string };

export function Legend({ items }: { items: LegendItem[] }) {
  return (
    <section
      data-legend
      aria-label={W.legendLabel}
      className="bg-card/60 rounded-lg border px-3 py-2.5"
    >
      <ul className="flex flex-wrap gap-x-5 gap-y-1.5 text-sm">
        {items.map((item) => (
          <li key={item.tag} data-tag={item.tag} className="flex min-w-0 items-start gap-2">
            <Swatch tone={item.tone} className="mt-1.5" />
            {/* One wrapping line: at 390 px a long explanation wraps instead of widening the page. */}
            <span className="min-w-0">
              <span className="font-medium">{item.name}</span>{" "}
              <span className="text-muted-foreground">{item.explain}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}

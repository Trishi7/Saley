// Today, part 4: "Saley suggests". Each card says what Saley suggests in one line, from the
// API's `line` ("Add Priya Rao (Sarvam) to pipeline — hiring for evals"; R5 T5). The line is
// built and value-checked on the server; the page never writes its own. A card opens the
// Suggestions list. Find people is off: where a card could use it, a "coming soon" badge shows.
import { ChevronRightIcon } from "lucide-react";
import { ClickCard } from "@/components/saley/ClickCard";
import { ComingSoon } from "@/components/saley/ComingSoon";
import { useFeatures } from "@/hooks/use-me";
import { hrefFor, type ViewLink } from "@/lib/format";
import { W } from "@/words";
import type { TodayData } from "./types";

const SUGGEST_LINK: ViewLink = { view: "suggest" };

export function Suggests({ block }: { block: TodayData["saley_suggests"] }) {
  const features = useFeatures();
  const findOff = features !== null && !features.find_people;
  const items = block.items.filter((item) => item.line);
  return (
    <section aria-labelledby="suggest-title" className="flex flex-col gap-3" id="suggests">
      <h2 id="suggest-title" className="text-base font-semibold">
        {W.suggestTitle}
      </h2>
      {items.length === 0 ? (
        <p className="empty text-muted-foreground bg-card rounded-lg border px-4 py-6 text-sm">
          {W.suggestEmpty}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {items.map((item) => (
            <li key={`${item.kind}-${item.id}`} className="min-w-0">
              <ClickCard
                href={hrefFor(item.link ?? SUGGEST_LINK)}
                kind="suggest"
                className="flex items-center gap-3 py-3"
              >
                {/* The line keeps the width; the badge goes under it, never beside it, so a
                    narrow card does not squeeze the line into one word per row (R5 T17). */}
                <span className="flex min-w-0 flex-1 flex-col items-start gap-1.5">
                  <span className="text-sm text-pretty">{item.line}</span>
                  {findOff && item.can_find_people && (
                    <span data-feature="find_people">
                      <ComingSoon feature="find_people" withName />
                    </span>
                  )}
                </span>
                <ChevronRightIcon
                  aria-hidden
                  className="text-muted-foreground size-4 shrink-0 transition-transform duration-150 group-hover/card:translate-x-0.5"
                />
              </ClickCard>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

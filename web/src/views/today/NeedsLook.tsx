// Today, part 3: "Needs a look now" (D158, D172, D173). Each card is ONE clickable element (a
// ClickCard link, `[data-kind]`) with a type badge: News, Event, Blocker or Waiting. No inner
// "Open" link. Only the kinds the API returns show; the old redirect-only card is gone.
// Mail replies are off until mail is set up: a "coming soon" badge says so (D164).
import { ChevronRightIcon } from "lucide-react";
import { ClickCard } from "@/components/saley/ClickCard";
import { ComingSoon } from "@/components/saley/ComingSoon";
import { Tag } from "@/components/saley/Tag";
import type { Tone } from "@/components/saley/tones";
import { hrefFor } from "@/lib/format";
import { W } from "@/words";
import type { LookKind, TodayData } from "./types";

const BADGE: Record<LookKind, { tone: Tone; name: string }> = {
  news: { tone: "info", name: W.lgNews },
  event: { tone: "primary", name: W.lgEvent },
  blocker: { tone: "bad", name: W.lgBlocker },
  waiting: { tone: "warn", name: W.lgWaiting },
};

export function NeedsLook({ block }: { block: TodayData["needs_look"] }) {
  return (
    <section aria-labelledby="look-title" className="flex flex-col gap-3" id="look">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="look-title" className="text-base font-semibold">
          {W.lookTitle}
        </h2>
        {block.soon === "mail_replies" && (
          <span data-feature="mail_replies">
            <ComingSoon feature="mail_replies" withName />
          </span>
        )}
      </div>
      {block.cards.length === 0 ? (
        <p className="empty text-muted-foreground bg-card rounded-lg border px-4 py-6 text-sm">
          {W.lookEmpty}
        </p>
      ) : (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {block.cards.map((card, i) => {
            const badge = BADGE[card.kind] ?? BADGE.news;
            return (
              <li key={`${card.kind}-${i}`} className="min-w-0">
                <ClickCard
                  href={hrefFor(card.link)}
                  kind={card.kind}
                  className="flex h-full items-start gap-3"
                >
                  <span className="flex min-w-0 flex-1 flex-col gap-1.5">
                    <Tag tag={card.kind} tone={badge.tone} className="w-fit">
                      {badge.name}
                    </Tag>
                    <span className="text-sm font-medium text-pretty">{card.title}</span>
                    <span className="text-muted-foreground text-xs text-pretty">{card.why}</span>
                  </span>
                  <ChevronRightIcon
                    aria-hidden
                    className="text-muted-foreground mt-1 size-4 shrink-0 transition-transform duration-150 group-hover/card:translate-x-0.5"
                  />
                </ClickCard>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

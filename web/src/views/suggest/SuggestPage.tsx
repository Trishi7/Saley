// Suggestions (R5 T15; docs/08 view 5). The four kinds (warm intros, best contact, new
// companies, researchers) are a filter group whose value lives in the hash (`#suggest?tab=newco`,
// the legacy link form). Each kind is a Gmail-model list (D157): New, 10 a page, and Seen
// earlier; the page marks the shown New items seen with item type `suggest_<kind>`.
// Hooks: `[data-kind-tabs] [data-filter]`, `#sg-list`.
import { useState } from "react";
import { useDrawerPair } from "@/components/saley/DrawerPair";
import { FilterBar } from "@/components/saley/FilterBar";
import { Pager } from "@/components/saley/Pager";
import { NothingNew, SeenMarkFailed, SeenTabs } from "@/components/saley/SeenTabs";
import { EmptyLine, ErrorLine, LoadingLine } from "@/components/saley/states";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { useRoute } from "@/hooks/use-route";
import { type SeenTab, useSeenList } from "@/hooks/use-seen-list";
import { go } from "@/lib/route";
import { fmt, W } from "@/words";
import { SuggestionCard } from "./SuggestionCard";
import type { Suggestion, SuggestData, SuggestKind } from "./types";

const KINDS: { id: SuggestKind; label: string }[] = [
  { id: "warm", label: W.sgTabWarm },
  { id: "best", label: W.sgTabBest },
  { id: "newco", label: W.sgTabNewco },
  { id: "res", label: W.sgTabRes },
];

function kindFrom(value: string | undefined): SuggestKind {
  return KINDS.find((k) => k.id === value)?.id ?? "warm";
}

export function SuggestPage({ view }: { view: ViewDef }) {
  const kind = kindFrom(useRoute().params.tab);
  const [tab, setTab] = useState<SeenTab>("new");
  const list = useSeenList<SuggestData>(`/api/suggest/${kind}`, tab, `suggest_${kind}`);
  const data = list.data;
  const { openPerson, openCompany, drawers } = useDrawerPair();
  const open = (item: Suggestion, from: HTMLElement) => {
    if (item.kind === "newco" && item.account_id !== null) {
      openCompany({ id: item.account_id, name: item.subject }, from);
    } else if (item.contact_id !== null) {
      openPerson({ contactId: item.contact_id, name: item.subject, company: item.company }, from);
    }
  };

  return (
    <ViewPage view={view}>
      <div data-content className="flex flex-col gap-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <SeenTabs
            tab={tab}
            newTotal={data?.new_total ?? null}
            seenTotal={data?.seen_total ?? null}
            onPick={setTab}
          />
          <div data-kind-tabs>
            <FilterBar
              options={KINDS}
              current={kind}
              onPick={(id) => go("suggest", id === "warm" ? undefined : { tab: id })}
              label={W.sgKindLabel}
            />
          </div>
        </div>
        {list.error && <ErrorLine what={W.whatSuggest} error={list.error} onRetry={list.reload} />}
        {!data && list.loading && <LoadingLine />}
        {list.markFailed && <SeenMarkFailed />}
        {data && (
          <>
            <p className="text-muted-foreground nums text-xs" data-stamp>
              {fmt(W.winLine, { w: data.window.label })}
              {data.capped_note ? ` · ${data.capped_note}` : ""}
            </p>
            <section
              className="bg-card min-w-0 rounded-lg border"
              aria-label={W.navSuggest}
              aria-busy={list.loading || undefined}
            >
              <Pager
                total={data.total}
                offset={list.offset}
                shown={data.items.length}
                onMove={list.move}
                label={W.navSuggest}
                className="pb-3 [&>nav]:px-4"
              >
                {data.items.length === 0 && tab === "new" && <NothingNew />}
                {data.items.length === 0 && tab === "seen" && (
                  <div className="px-4 py-6">
                    <EmptyLine computed={data.last_computed_label} />
                  </div>
                )}
                {data.items.length > 0 && (
                  <ul id="sg-list" className="divide-y">
                    {data.items.map((item) => (
                      <SuggestionCard key={`${item.kind}-${item.id}`} item={item} onOpen={open} />
                    ))}
                  </ul>
                )}
              </Pager>
            </section>
          </>
        )}
      </div>
      {drawers}
    </ViewPage>
  );
}

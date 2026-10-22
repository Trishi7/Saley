// Signals (R5 T15; docs/08 view 6). Order: legend (ViewPage), the New / Seen earlier tabs with
// their counts (the page's numbers), the filters, then the list, 10 a page (Gmail model, D157).
// Filters: All news or Linked to us, the six categories by name (D156), the time window, the
// tag, the source text and (Linked) the company or person. Hooks: `[data-sig-tabs]`,
// `[data-cat-chips] [data-filter]`, `[data-range-chips] [data-filter]`, `#sig-tag`, `#sig-src`,
// `#sig-entity`, `#sig-list`.
import { useState } from "react";
import { FilterBar, SearchBox } from "@/components/saley/FilterBar";
import { Pager } from "@/components/saley/Pager";
import { NothingNew, SeenMarkFailed, SeenTabs } from "@/components/saley/SeenTabs";
import { EmptyLine, ErrorLine, LoadingLine } from "@/components/saley/states";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { useRoute } from "@/hooks/use-route";
import { type SeenTab, useSeenList } from "@/hooks/use-seen-list";
import { go } from "@/lib/route";
import { fmt, W } from "@/words";
import {
  ALL,
  CATEGORIES,
  filtersFrom,
  NEWS_TAGS,
  paramsFor,
  RANGES,
  seenType,
  signalsBase,
  type SignalFilters,
  TABS,
} from "./signal-rules";
import { SignalRow } from "./SignalRow";
import type { SignalsData } from "./types";

const SELECT =
  "border-input bg-background h-10 rounded-md border px-3 text-sm outline-none " +
  "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]";

export function SignalsPage({ view }: { view: ViewDef }) {
  const filters = filtersFrom(useRoute().params);
  const [src, setSrc] = useState("");
  const [entity, setEntity] = useState("");
  const [tab, setTab] = useState<SeenTab>("new");
  const base = signalsBase(filters, src, entity);
  const list = useSeenList<SignalsData>(base, tab, seenType(filters.tab));
  const data = list.data;
  const set = (change: Partial<SignalFilters>) => go("signals", paramsFor({ ...filters, ...change }));

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
          <div data-sig-tabs>
            <FilterBar
              options={TABS}
              current={filters.tab}
              onPick={(id) => set({ tab: id === "linked" ? "linked" : "all" })}
              label={W.sigViewLabel}
            />
          </div>
        </div>
        <section
          aria-label={W.filtersLabel}
          className="bg-card/60 flex flex-col gap-3 rounded-lg border p-3"
        >
          <div data-cat-chips>
            <FilterBar
              options={[{ id: ALL, label: W.tabAll }, ...CATEGORIES]}
              current={filters.cat || ALL}
              onPick={(id) => set({ cat: id === ALL ? "" : id })}
              label={W.catLabel}
            />
          </div>
          <div className="flex flex-col gap-3 lg:flex-row lg:flex-wrap lg:items-center">
            <div data-range-chips>
              <FilterBar
                options={RANGES}
                current={filters.day ? "" : filters.range}
                onPick={(id) => set({ range: id, day: "" })}
                label={W.rangeLabel}
              />
            </div>
            <select
              id="sig-tag"
              aria-label={W.tagLabel}
              value={filters.tag}
              onChange={(e) => set({ tag: e.target.value })}
              className={SELECT}
            >
              <option value="">{W.tagAny}</option>
              {NEWS_TAGS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <SearchBox id="sig-src" label={W.srcLabel} hint={W.srcHint} onSearch={setSrc} />
            {filters.tab === "linked" && (
              <SearchBox
                id="sig-entity"
                label={W.entityLabel}
                hint={W.entityHint}
                onSearch={setEntity}
              />
            )}
          </div>
        </section>
        {list.error && <ErrorLine what={W.whatSignals} error={list.error} onRetry={list.reload} />}
        {!data && list.loading && <LoadingLine />}
        {list.markFailed && <SeenMarkFailed />}
        {data && (
          <>
            <p className="text-muted-foreground nums text-xs" data-stamp>
              {fmt(W.winLine, { w: data.window.label })}
              {" · "}
              {data.last_computed ? fmt(W.lastComputed, { t: data.last_computed_label }) : W.noNewsYet}
            </p>
            <section
              className="bg-card min-w-0 rounded-lg border"
              aria-label={W.navSignals}
              aria-busy={list.loading || undefined}
            >
              <Pager
                total={data.total}
                offset={list.offset}
                shown={data.items.length}
                onMove={list.move}
                label={W.navSignals}
                className="pb-3 [&>nav]:px-4"
              >
                {data.items.length === 0 && tab === "new" && <NothingNew />}
                {data.items.length === 0 && tab === "seen" && (
                  <div className="px-4 py-6">
                    <EmptyLine computed={data.last_computed_label} />
                  </div>
                )}
                {data.items.length > 0 && (
                  <ul id="sig-list" className="divide-y">
                    {data.items.map((item) => (
                      <SignalRow key={item.id} item={item} />
                    ))}
                  </ul>
                )}
              </Pager>
            </section>
          </>
        )}
      </div>
    </ViewPage>
  );
}

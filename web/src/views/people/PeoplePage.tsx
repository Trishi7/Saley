// The People page of the dashboard v2 (R5 T14; docs/08 view 4). Order: the legend (ViewPage),
// the counts line, the filters with their counts and the page search, then the list, 10 per
// page with "N more" (Pager). A row opens the person drawer (`.drawer.on`). The filter lives in
// the hash (`#people?filter=p1_waiting`): the Today funnel bars and tiles link here (D147).
import { useCallback, useState } from "react";
import { FilterBar, SearchBox } from "@/components/saley/FilterBar";
import { Pager } from "@/components/saley/Pager";
import { PersonDrawer, type DrawerPerson, type SavedEmail } from "@/components/saley/PersonDrawer";
import { EmptyLine, ErrorLine, LoadingLine } from "@/components/saley/states";
import { useToast } from "@/components/saley/Toaster";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { useApi } from "@/hooks/use-api";
import { useKeptPage, useOffset } from "@/hooks/use-kept";
import { useRoute } from "@/hooks/use-route";
import { go } from "@/lib/route";
import { fmt, W } from "@/words";
import { DEFAULT_FILTER, peopleFilters, peoplePath } from "./people-rules";
import { PeopleTable } from "./PeopleTable";
import type { PeopleData } from "./types";

export function PeoplePage({ view }: { view: ViewDef }) {
  const toast = useToast();
  const filter = useRoute().params.filter || DEFAULT_FILTER;
  const [q, setQ] = useState("");
  const [offset, setOffset] = useOffset(`${filter}|${q}`);
  const { data, error, loading, reload } = useApi<PeopleData>(peoplePath(filter, q, offset));
  const kept = useKeptPage(data, offset);
  const shown = kept?.page;

  const [person, setPerson] = useState<DrawerPerson | null>(null);
  const [opener, setOpener] = useState<HTMLElement | null>(null); // gets the focus on close
  const pick = (next: string) => go("people", next === DEFAULT_FILTER ? undefined : { filter: next });
  const onSaved = useCallback(
    (saved: SavedEmail) => {
      setPerson(null);
      toast(saved.label);
      reload(); // the "Connected, no email" list and its count change
    },
    [reload, toast],
  );

  const c = shown?.counts;
  return (
    <ViewPage view={view}>
      {error && <ErrorLine what={W.whatPeople} error={error} onRetry={reload} />}
      {!shown && loading && <LoadingLine />}
      {shown && c && (
        <div data-content className="flex flex-col gap-5" aria-busy={loading || undefined}>
          <p className="text-muted-foreground nums text-sm" data-stamp>
            {fmt(W.pplStamp, { n: c.people, m: c.with_notes, p: c.with_paper })}
          </p>
          <div className="flex flex-col gap-3 xl:flex-row xl:items-center xl:justify-between">
            <FilterBar
              options={peopleFilters(filter, c)}
              current={filter}
              onPick={pick}
              label={W.filtersLabel}
            />
            <SearchBox id="ppl-search" label={W.pplSearch} hint={W.pplSearchHint} onSearch={setQ} />
          </div>
          <section className="bg-card min-w-0 rounded-lg border" aria-label={W.navPeople}>
            <Pager
              total={shown.total}
              offset={kept?.offset ?? offset}
              shown={shown.items.length}
              onMove={setOffset}
              label={W.navPeople}
              className="pb-3 [&>nav]:px-4"
            >
              {shown.items.length === 0 ? (
                <div className="px-4 py-6">
                  <EmptyLine computed={shown.last_computed_label} />
                </div>
              ) : (
                <PeopleTable
                  items={shown.items}
                  onOpen={(p, from) => {
                    setOpener(from);
                    setPerson({ contactId: p.id, name: p.name, company: p.company });
                  }}
                />
              )}
            </Pager>
          </section>
        </div>
      )}
      <PersonDrawer
        person={person}
        onClose={() => setPerson(null)}
        onSaved={onSaved}
        returnTo={opener}
      />
    </ViewPage>
  );
}

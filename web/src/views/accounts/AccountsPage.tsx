// The Accounts page of the dashboard v2 (R5 T14; docs/08 view 3). Order: the legend (ViewPage),
// the coverage numbers (stamp line and industry bars), then the filters, the page search and
// the list, 10 per page with "N more" (Pager). A row opens the company drawer; a person in it
// opens the person drawer. The filter lives in the hash (`#accounts?filter=p1`), so the
// legacy links keep working (D147).
import { useCallback, useState } from "react";
import { CompanyDrawer, type DrawerCompany } from "@/views/accounts/CompanyDrawer";
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
import { accountFilters, accountsPath, DEFAULT_FILTER } from "./account-rules";
import { AccountTable } from "./AccountTable";
import { IndustryBars } from "./IndustryBars";
import type { AccountsData } from "./types";

export function AccountsPage({ view }: { view: ViewDef }) {
  const toast = useToast();
  const filter = useRoute().params.filter || DEFAULT_FILTER;
  const [q, setQ] = useState("");
  const [offset, setOffset] = useOffset(`${filter}|${q}`);
  const { data, error, loading, reload } = useApi<AccountsData>(accountsPath(filter, q, offset));
  const kept = useKeptPage(data, offset);
  const shown = kept?.page;

  const [company, setCompany] = useState<DrawerCompany | null>(null);
  const [person, setPerson] = useState<DrawerPerson | null>(null);
  // Focus goes back to the company name Button that opened the drawer (also after the
  // company -> person hand-off); during the hand-off the company drawer leaves focus alone.
  const [opener, setOpener] = useState<HTMLElement | null>(null);
  const [handOff, setHandOff] = useState(false);
  const pick = (next: string) => go("accounts", next === DEFAULT_FILTER ? undefined : { filter: next });
  const onSaved = useCallback(
    (saved: SavedEmail) => {
      setPerson(null);
      toast(saved.label);
    },
    [toast],
  );

  return (
    <ViewPage view={view}>
      {error && <ErrorLine what={W.whatAccounts} error={error} onRetry={reload} />}
      {!shown && loading && <LoadingLine />}
      {shown && (
        <div data-content className="flex flex-col gap-5" aria-busy={loading || undefined}>
          <p className="text-muted-foreground nums text-sm" data-stamp>
            {fmt(W.accStamp, { n: shown.with_people, m: shown.in_master })}
          </p>
          <IndustryBars industries={shown.industries} current={filter} onPick={pick} />
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <FilterBar
              options={accountFilters(filter)}
              current={filter}
              onPick={pick}
              label={W.filtersLabel}
            />
            <SearchBox id="acc-search" label={W.accSearch} hint={W.accSearchHint} onSearch={setQ} />
          </div>
          <section className="bg-card min-w-0 rounded-lg border" aria-label={W.navAccounts}>
            <Pager
              total={shown.total}
              offset={kept?.offset ?? offset}
              shown={shown.items.length}
              onMove={setOffset}
              label={W.navAccounts}
              className="pb-3 [&>nav]:px-4"
            >
              {shown.items.length === 0 ? (
                <div className="px-4 py-6">
                  <EmptyLine computed={shown.last_computed_label} />
                </div>
              ) : (
                <AccountTable
                  items={shown.items}
                  onOpen={(a, from) => {
                    setOpener(from);
                    setHandOff(false);
                    setCompany({ id: a.id, name: a.name });
                  }}
                />
              )}
            </Pager>
          </section>
        </div>
      )}
      <CompanyDrawer
        company={company}
        onClose={() => setCompany(null)}
        returnTo={handOff ? null : opener}
        onOpenPerson={(p, name) => {
          setHandOff(true);
          setCompany(null);
          setPerson({ contactId: p.id, name: p.name, company: name });
        }}
      />
      <PersonDrawer
        person={person}
        onClose={() => setPerson(null)}
        onSaved={onSaved}
        returnTo={opener}
      />
    </ViewPage>
  );
}

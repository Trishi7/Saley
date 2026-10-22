// The company drawer (Appendix B hook `.drawer.on`; R5-A03). It shows the account tags,
// "13 people · 9 P1" with the stage bar, a few facts (no Evidence row: brief), the people of
// the company as rows that open the person drawer, the touches as the timeline strip (one
// row per month, D-R5 brief) and the stage history. One GET /api/accounts/{id}.
import { ChevronRightIcon } from "lucide-react";
import { returnFocus, useLastShown } from "@/components/saley/drawer-focus";
import { StageBar } from "@/components/saley/StageBar";
import { StageTag } from "@/components/saley/StageTag";
import { Tag } from "@/components/saley/Tag";
import { TouchTimeline } from "@/components/saley/TouchTimeline";
import { ErrorLine, LoadingLine } from "@/components/saley/states";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { useApi } from "@/hooks/use-api";
import { cn } from "@/lib/utils";
import { fmt, W } from "@/words";
import { DrawerSection, FactList, History } from "@/views/people/DrawerParts";
import { accountTag } from "./account-rules";
import type { AccountData, AccountDetail, AccountPerson } from "./types";

export type DrawerCompany = { id: number; name: string };

export function CompanyDrawer({
  company: current,
  onClose,
  onOpenPerson,
  returnTo,
}: {
  company: DrawerCompany | null;
  onClose: () => void;
  onOpenPerson: (person: AccountPerson, company: string) => void;
  /** The control that gets the focus back on close; null during the person hand-off. */
  returnTo?: HTMLElement | null;
}) {
  const open = current !== null;
  const company = useLastShown(current); // stays rendered while the sheet slides out
  return (
    <Sheet open={open} onOpenChange={(next) => !next && onClose()}>
      <SheetContent
        className={cn("drawer w-full gap-0 overflow-y-auto sm:max-w-lg", open && "on")}
        data-drawer="company"
        onCloseAutoFocus={(event) => returnFocus(event, returnTo)}
      >
        {company && (
          <CompanyBody key={company.id} company={company} onOpenPerson={onOpenPerson} />
        )}
      </SheetContent>
    </Sheet>
  );
}

function CompanyBody({
  company,
  onOpenPerson,
}: {
  company: DrawerCompany;
  onOpenPerson: (person: AccountPerson, company: string) => void;
}) {
  const { data, error, loading, reload } = useApi<AccountData>(`/api/accounts/${company.id}`);
  const item = data?.item;
  return (
    <>
      <SheetHeader className="border-b pr-12">
        <SheetTitle className="text-lg text-balance">{item?.name ?? company.name}</SheetTitle>
        <SheetDescription>
          {item ? `${item.industry || "—"} · ${item.geo || "—"}` : "—"}
        </SheetDescription>
      </SheetHeader>
      <div className="flex flex-col gap-6 p-4">
        {error && <ErrorLine what={W.whatCompany} error={error} onRetry={reload} />}
        {!item && loading && <LoadingLine />}
        {item && <CompanyDetail item={item} onOpenPerson={onOpenPerson} />}
      </div>
    </>
  );
}

function CompanyDetail({
  item,
  onOpenPerson,
}: {
  item: AccountDetail;
  onOpenPerson: (person: AccountPerson, company: string) => void;
}) {
  return (
    <>
      <div className="flex flex-col gap-2">
        {item.tags.length > 0 && (
          <span className="flex flex-wrap gap-1">
            {item.tags.map((name) => {
              const spec = accountTag(name);
              return (
                <Tag key={name} tag={spec.tag} tone={spec.tone}>
                  {name}
                </Tag>
              );
            })}
          </span>
        )}
        <span data-people-label className="nums text-sm font-medium">
          {item.people_label}
        </span>
        <StageBar counts={item.stage_counts} />
      </div>
      <FactList
        facts={[
          [W.dCountry, item.geo],
          [W.dFunding, item.funding],
          [W.dGoals, item.goal_codes.length ? item.goal_codes.join(", ") : null],
          [W.dInMaster, item.in_master ? W.yes : W.no],
        ]}
      />
      <DrawerSection title={W.dPeople}>
        {item.people_list.length === 0 ? (
          <p className="text-muted-foreground text-sm">{W.noPeople}</p>
        ) : (
          <ul className="flex flex-col divide-y rounded-md border" data-company-people>
            {item.people_list.map((p) => (
              <li key={p.id}>
                <button
                  type="button"
                  data-company-person={p.id}
                  aria-label={fmt(W.openPerson, { name: p.name })}
                  onClick={() => onOpenPerson(p, item.name)}
                  className="hover:bg-accent/60 focus-visible:ring-ring/50 group flex w-full items-center gap-3 px-3 py-2 text-left text-sm outline-none focus-visible:ring-[3px]"
                >
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-medium">{p.name}</span>
                    <span className="text-muted-foreground truncate text-xs">
                      {p.title || "—"}
                    </span>
                  </span>
                  <StageTag stage={p.stage} />
                  <ChevronRightIcon
                    aria-hidden
                    className="text-muted-foreground size-4 shrink-0 transition-transform duration-150 group-hover:translate-x-0.5"
                  />
                </button>
              </li>
            ))}
          </ul>
        )}
      </DrawerSection>
      <DrawerSection title={W.dTouches}>
        <TouchTimeline timeline={item.timeline} />
      </DrawerSection>
      <DrawerSection title={W.dHistory}>
        <History entries={item.stage_history} />
      </DrawerSection>
    </>
  );
}

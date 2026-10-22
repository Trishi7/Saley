// Money blocked (R5 T15; docs/08 "Money tile/page", D42). Numbers first: the known deal value
// that waits for a late item ("—" when no blocked deal has a value, never 0), the deals and the
// deals with no value on file. Then the deals, 10 a page; a row opens the person drawer (T14).
// Hooks: `[data-stat]`, `#money-table`, `[data-row][data-deal]`, `[data-open-person]`.
import { ArrowLeftIcon } from "lucide-react";
import { useDrawerPair } from "@/components/saley/DrawerPair";
import { OneLine } from "@/components/saley/Links";
import { Pager, PAGE_SIZE } from "@/components/saley/Pager";
import { StageTag } from "@/components/saley/StageTag";
import { Stat } from "@/components/saley/Stat";
import { EmptyLine, ErrorLine, LoadingLine } from "@/components/saley/states";
import { Tag } from "@/components/saley/Tag";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useApi } from "@/hooks/use-api";
import { useKeptPage, useOffset } from "@/hooks/use-kept";
import { cn } from "@/lib/utils";
import { lateTag } from "@/views/goals/goal-rules";
import { fmt, W } from "@/words";
import type { MoneyData, MoneyItem } from "./types";

/** "USD 120,000", or null (shown "—") when no blocked deal has a value (app.money_value). */
function totalValue(d: MoneyData): string | null {
  const known = d.total - d.no_value;
  if (d.total > 0 && known === 0) return null;
  return fmt(W.usd, { v: d.total_usd.toLocaleString("en-US") });
}

function MoneyTable({
  items,
  onOpen,
}: {
  items: MoneyItem[];
  onOpen: (m: MoneyItem, from: HTMLElement | null) => void;
}) {
  return (
    <Table id="money-table" className="min-w-[48rem] table-fixed">
      <TableHeader>
        <TableRow className="hover:bg-transparent">
          <TableHead className="w-[26%]">{W.colDeal}</TableHead>
          <TableHead className="w-[16%] text-right">{W.colValue}</TableHead>
          <TableHead className="w-[16%]">{W.colCompany}</TableHead>
          <TableHead>{W.colBlocking}</TableHead>
          <TableHead className="w-[12%]">{W.colOwner}</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((m) => {
          const late = lateTag(m.late_by_days);
          return (
            <TableRow
              key={m.contact_id}
              data-row
              data-deal={m.contact_id}
              className="cursor-pointer"
              onClick={(e) =>
                onOpen(m, e.currentTarget.querySelector<HTMLElement>("[data-open-person]"))
              }
            >
              <TableCell className="py-2.5">
                <span className="flex min-w-0 flex-col items-start gap-1">
                  <Button
                    type="button"
                    variant="link"
                    data-open-person
                    aria-label={fmt(W.openPerson, { name: m.name })}
                    className="text-foreground hover:text-primary h-auto max-w-full justify-start p-0 font-semibold"
                    onClick={(e) => {
                      e.stopPropagation();
                      onOpen(m, e.currentTarget);
                    }}
                  >
                    <span className="truncate">{m.name}</span>
                  </Button>
                  <StageTag stage={m.stage} />
                </span>
              </TableCell>
              <TableCell
                className={cn("tabular py-2.5 text-right", m.size_usd === null && "text-muted-foreground")}
              >
                {m.value_label}
              </TableCell>
              <TableCell className="text-muted-foreground py-2.5">
                <OneLine text={m.company} />
              </TableCell>
              <TableCell className="py-2.5">
                <span className="flex min-w-0 flex-col gap-1">
                  <OneLine text={m.deliverable} />
                  <span className="flex flex-wrap items-center gap-1.5">
                    {late && (
                      <Tag tag={late.tag} tone={late.tone} className="nums">
                        {late.text}
                      </Tag>
                    )}
                    {m.more_blocking > 0 && (
                      <span className="text-muted-foreground text-xs">
                        {fmt(W.moneyMore, { n: m.more_blocking })}
                      </span>
                    )}
                  </span>
                </span>
              </TableCell>
              <TableCell className="text-muted-foreground py-2.5">
                <OneLine text={m.owner} />
              </TableCell>
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}

export function MoneyPage({ view }: { view: ViewDef }) {
  const [offset, setOffset] = useOffset("money");
  const { data, error, loading, reload } = useApi<MoneyData>(
    `/api/money-blocked?limit=${PAGE_SIZE}&offset=${offset}`,
  );
  const kept = useKeptPage(data, offset);
  const shown = kept?.page;
  const { openPerson, drawers } = useDrawerPair();
  const open = (m: MoneyItem, from: HTMLElement | null) =>
    openPerson({ contactId: m.contact_id, name: m.name, company: m.company }, from);

  return (
    <ViewPage view={view}>
      <Button asChild variant="outline" size="sm" className="w-fit">
        <a href="#blockers" data-back="blockers">
          <ArrowLeftIcon aria-hidden />
          {W.moneyBack}
        </a>
      </Button>
      {error && <ErrorLine what={W.whatMoney} error={error} onRetry={reload} />}
      {!shown && loading && <LoadingLine />}
      {shown && (
        <div data-content className="flex flex-col gap-5">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 [&>*:last-child:nth-child(odd)]:col-span-2 sm:[&>*:last-child:nth-child(odd)]:col-span-1">
            <Stat id="value" label={W.moneyValue} value={totalValue(shown)} />
            <Stat id="deals" label={W.moneyDeals} value={shown.total} />
            <Stat id="no-value" label={W.moneyNoValue} value={shown.no_value} />
          </div>
          <section className="bg-card min-w-0 rounded-lg border" aria-label={W.navMoney} aria-busy={loading || undefined}>
            <Pager
              total={shown.total}
              offset={kept?.offset ?? offset}
              shown={shown.items.length}
              onMove={setOffset}
              label={W.navMoney}
              className="pb-3 [&>nav]:px-4"
            >
              {shown.items.length === 0 ? (
                <div className="px-4 py-6">
                  <EmptyLine computed={shown.last_computed_label} />
                </div>
              ) : (
                <MoneyTable items={shown.items} onOpen={open} />
              )}
            </Pager>
          </section>
        </div>
      )}
      {drawers}
    </ViewPage>
  );
}

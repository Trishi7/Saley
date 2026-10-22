// Events (R5 T15; docs/08 view 8, D160). Upcoming or past, each a Gmail-model list (D157): New,
// 10 a page, and Seen earlier. The server sorts registered events first, then by date; a
// registered event wears the "Registered" tag. City (Bangalore, London or Other) and Venue (the
// full address) are two columns. Speaker matches are off: the page shows
// `[data-soon="speaker_match"]` (ViewPage strip). The event link is an external Button.
// Hooks: `#ev-table`, `th[data-col]`, `tr[data-row][data-event][data-registered]`, `td[data-city]`,
// `td[data-venue]`, `[data-when-tabs] [data-filter]`.
import { useState } from "react";
import { ExternalButton, OneLine } from "@/components/saley/Links";
import { FilterBar } from "@/components/saley/FilterBar";
import { Pager } from "@/components/saley/Pager";
import { NothingNew, SeenMarkFailed, SeenTabs } from "@/components/saley/SeenTabs";
import { EmptyLine, ErrorLine, LoadingLine } from "@/components/saley/states";
import { Tag } from "@/components/saley/Tag";
import type { ViewDef } from "@/components/saley/views";
import { ViewPage } from "@/components/saley/ViewPage";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { type SeenTab, useSeenList } from "@/hooks/use-seen-list";
import { fmt, W } from "@/words";
import type { EventItem, EventsData } from "./types";

const WHEN = [
  { id: "upcoming", label: W.evTabUp },
  { id: "past", label: W.evTabPast },
];

function EventRow({ e }: { e: EventItem }) {
  return (
    <TableRow data-row data-event={e.id} data-registered={e.registered ? "1" : "0"}>
      <TableCell className="py-2.5">
        <span className="flex min-w-0 flex-col items-start gap-1">
          <span className="flex max-w-full flex-wrap items-center gap-1.5">
            <span className="min-w-0 font-medium text-pretty">{e.name}</span>
            {e.registered && (
              <Tag tag="registered" tone="good">
                {W.lgRegistered}
              </Tag>
            )}
          </span>
          {e.href && (
            <ExternalButton href={e.href} label={fmt(W.evOpenAria, { e: e.name })}>
              {W.evOpen}
            </ExternalButton>
          )}
        </span>
      </TableCell>
      <TableCell data-city className="py-2.5">
        {e.city}
      </TableCell>
      <TableCell data-venue className="text-muted-foreground py-2.5">
        <OneLine text={e.venue} />
      </TableCell>
      <TableCell className="py-2.5">
        <span className="flex flex-col">
          <span className="nums text-sm">{e.date_label}</span>
          <span className="text-muted-foreground text-xs">{e.days_text}</span>
        </span>
      </TableCell>
      <TableCell className="tabular py-2.5 text-right">{e.people_in_city}</TableCell>
      <TableCell className="tabular py-2.5 text-right">{e.connected}</TableCell>
      <TableCell className="tabular py-2.5 text-right">{e.p1}</TableCell>
    </TableRow>
  );
}

function EventTable({ items }: { items: EventItem[] }) {
  return (
    <Table id="ev-table" className="min-w-[52rem] table-fixed">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead data-col="event" className="w-[26%]">{W.evName}</TableHead>
            <TableHead data-col="city" className="w-[11%]">{W.evCity}</TableHead>
            <TableHead data-col="venue">{W.evVenue}</TableHead>
            <TableHead data-col="dates" className="w-[15%]">{W.evDates}</TableHead>
            <TableHead data-col="people" className="w-[9%] text-right">{W.evPeople}</TableHead>
            <TableHead data-col="connected" className="w-[10%] text-right">{W.evConnected}</TableHead>
            <TableHead data-col="p1" className="w-[6%] text-right">{W.lgP1}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {items.map((e) => (
            <EventRow key={e.id} e={e} />
          ))}
        </TableBody>
      </Table>
  );
}

export function EventsPage({ view }: { view: ViewDef }) {
  const [when, setWhen] = useState("upcoming");
  const [tab, setTab] = useState<SeenTab>("new");
  const list = useSeenList<EventsData>(`/api/events?when=${when}`, tab, "event");
  const data = list.data;

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
          <div data-when-tabs>
            <FilterBar options={WHEN} current={when} onPick={setWhen} label={W.evWhenLabel} />
          </div>
        </div>
        {list.error && <ErrorLine what={W.whatEvents} error={list.error} onRetry={list.reload} />}
        {!data && list.loading && <LoadingLine />}
        {list.markFailed && <SeenMarkFailed />}
        {data && (
          <>
            <p className="text-muted-foreground nums text-xs" data-stamp>
              {fmt(W.winLine, { w: data.window.label })}
            </p>
            <section
              className="bg-card min-w-0 rounded-lg border"
              aria-label={W.navEvents}
              aria-busy={list.loading || undefined}
            >
              <Pager
                total={data.total}
                offset={list.offset}
                shown={data.items.length}
                onMove={list.move}
                label={W.navEvents}
                className="pb-3 [&>nav]:px-4"
              >
                {data.items.length === 0 && tab === "new" && <NothingNew />}
                {data.items.length === 0 && tab === "seen" && (
                  <div className="px-4 py-6">
                    <EmptyLine computed={data.last_computed_label} />
                  </div>
                )}
                {data.items.length > 0 && <EventTable items={data.items} />}
              </Pager>
            </section>
          </>
        )}
      </div>
    </ViewPage>
  );
}
